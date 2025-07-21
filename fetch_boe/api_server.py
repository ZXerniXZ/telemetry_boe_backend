# telemetry_backend.py  – backend FastAPI completo
# ------------------------------------------------
import os
import threading
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import nmap
from concurrent.futures import ThreadPoolExecutor
import time
import atexit
from fastapi.middleware.cors import CORSMiddleware
from paho.mqtt import client as mqtt
from pymavlink import mavutil
import logging
from dotenv import load_dotenv
load_dotenv()
from enum import Enum
import signal
import sys
import math

# logica esistente
from fetch_boe.shared import (
    MQTT_PORT, MQTT_TOPIC_FMT, MQTT_QOS, MQTT_RETAIN, LOG_LEVEL, PUBLISH_INTERVAL, HEARTBEAT_HZ, HEARTBEAT_PERIOD, DEVICE_TIMEOUT,
    VAI_A_RETRY_SEC, VAI_A_TOLERANCE_METERS, VAI_A_TIMEOUT_SEC
)

# ----------------------------------------------------------------------
# CONFIG & APP
# ----------------------------------------------------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SCAN_SUBNET = os.environ.get("SCAN_SUBNET", "10.8.0.0/24")
MIN_LAST_OCTET = int(os.environ.get("MIN_LAST_OCTET", "30"))

serializer_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="json")

# {boa_key: (thread, stop_event)}
active_boes = {}
active_boes_lock = threading.Lock()

active_vaia = {}  # {boa_key: (thread, stop_event)}
active_vaia_lock = threading.Lock()

logger = logging.getLogger("telemetry_backend")
logging.basicConfig(level=logging.INFO)

# ----------------------------------------------------------------------
# Helper MAVLink per parametri
# ----------------------------------------------------------------------
def _pid_to_str(pid):
    if isinstance(pid, (bytes, bytearray)):
        return pid.decode(errors="ignore").rstrip("\x00")
    return str(pid).rstrip("\x00")


def _name_to_pid(name: str):
    return name.encode()[:16]


def _set_param(ip: str, port: int, param_name: str, value: float,
               timeout: float = 2.0):
    """
    Apre un link udpout:ip:port, aspetta heartbeat, scrive param_name=value,
    chiude il link. Lancia eccezione se fallisce.
    """
    mav = mavutil.mavlink_connection(
        f"udpout:{ip}:{port}",
        dialect="ardupilotmega",
        source_system=255,
        autoreconnect=True,
        timeout=2,
    )

    # ping iniziale per far aprire il ritorno UDP
    for _ in range(3):
        mav.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                               mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                               0, 0, 0)
        time.sleep(0.2)

    mav.wait_heartbeat(timeout=2)

    # prova a scoprire il tipo attuale
    try:
        mav.mav.param_request_read_send(mav.target_system,
                                        mav.target_component,
                                        _name_to_pid(param_name), -1)
        msg = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=2)
        ptype = msg.param_type if msg else mavutil.mavlink.MAV_PARAM_TYPE_REAL32
    except Exception:
        ptype = mavutil.mavlink.MAV_PARAM_TYPE_REAL32

    # invia PARAM_SET
    mav.mav.param_set_send(mav.target_system, mav.target_component,
                           _name_to_pid(param_name), float(value), ptype)

    # attende echo
    deadline = time.time() + timeout
    ok = False
    while time.time() < deadline:
        msg = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
        if msg and _pid_to_str(msg.param_id) == param_name:
            ok = abs(msg.param_value - float(value)) < 1e-4
            break
    mav.close()
    if not ok:
        raise RuntimeError(f"Set {param_name}={value} non confermato")


# ----------------------------------------------------------------------
# Thread wrapper con SYSID switch
# ----------------------------------------------------------------------
def thread_wrapper(ip: str, port: int, pool, stop_event: threading.Event):
    boa_key = f"{ip}:{port}"
    from fetch_boe.fetch_script import handle_device
    was_online = False
    fail_count = 0
    MAX_FAILS = 3
    while not stop_event.is_set():
        sysid_set = False
        try:
            logger.info(f"[{boa_key}] Tentativo connessione e set SYSID_THISMAV → 21")
            _set_param(ip, port, "SYSID_THISMAV", 21, timeout=2.0)
            sysid_set = True
            logger.info(f"[{boa_key}] SYSID_THISMAV impostato. Avvio telemetria.")
            if not was_online:
                publish_isonline(ip, port, True)
                was_online = True
            fail_count = 0
            handle_device(ip, port, pool, stop_event=stop_event)
        except Exception as e:
            logger.error(f"[{boa_key}] Errore/Disconnessione: {e}")
            fail_count += 1
            if fail_count >= MAX_FAILS and was_online:
                publish_isonline(ip, port, False)
                was_online = False
            # Attendi 1 secondo prima di riprovare
            for _ in range(10):
                if stop_event.is_set():
                    break
                time.sleep(0.1)
        finally:
            # Ripristina SYSID solo se era stato impostato in questa iterazione
            if sysid_set:
                try:
                    logger.info(f"[{boa_key}] Ripristino SYSID_THISMAV → 1")
                    _set_param(ip, port, "SYSID_THISMAV", 1, timeout=2.0)
                except Exception as e:
                    logger.warning(f"[{boa_key}] Ripristino SYSID fallito: {e}")
    # Uscita dal ciclo: sempre offline
    publish_isonline(ip, port, False)
    with active_boes_lock:
        active_boes.pop(boa_key, None)
    logger.info(f"[{boa_key}] Thread terminato.")


# ----------------------------------------------------------------------
# Shutdown: marca tutte le boe offline
# ----------------------------------------------------------------------
def set_all_boes_offline():
    for boa_key in list(active_boes.keys()):
        ip, port = boa_key.split(":")
        topic_base = MQTT_TOPIC_FMT.format(f"{ip.replace('.', '_')}_{port}")
        isonline_topic = f"{topic_base}/isonline"
        client = mqtt.Client(client_id=f"shutdown_{ip}_{port}", clean_session=True)
        try:
            client.connect("localhost", MQTT_PORT, keepalive=5)
            client.loop_start()
            client.publish(isonline_topic, "false", qos=MQTT_QOS, retain=True)
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

atexit.register(set_all_boes_offline)

def handle_shutdown_signal(signum, frame):
    logger.info(f"Ricevuto segnale {signum}, setto tutte le boe offline prima di uscire...")
    set_all_boes_offline()
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_shutdown_signal)
signal.signal(signal.SIGINT, handle_shutdown_signal)

# ----------------------------------------------------------------------
# API MODELS
# ----------------------------------------------------------------------
class BoaRequest(BaseModel):
    ip: str
    port: int


# ----------------------------------------------------------------------
# ROUTES
# ----------------------------------------------------------------------
@app.get("/scan")
def scan_boes():
    nm = nmap.PortScanner()
    nm.scan(hosts=SCAN_SUBNET, arguments='-sn -n -T4')
    found = []
    for host in nm.all_hosts():
        try:
            last_octet = int(host.strip().split('.')[-1])
            if last_octet > MIN_LAST_OCTET:
                found.append(host)
        except Exception:
            continue
    logger.info(f"[scan] Risultato scan: {found}")
    return {"found": found}


@app.post("/aggiungiboa")
def aggiungi_boa(req: BoaRequest):
    boa_key = f"{req.ip}:{req.port}"
    with active_boes_lock:
        if boa_key in active_boes:
            raise HTTPException(400, "Boa già monitorata")

        stop_event = threading.Event()
        t = threading.Thread(
            target=thread_wrapper,
            name=f"dev-{boa_key}",
            args=(req.ip, req.port, serializer_pool, stop_event),
            daemon=True,
        )
        t.start()
        active_boes[boa_key] = (t, stop_event)
    logger.info(f"[{boa_key}] Boa aggiunta e thread avviato")
    return {"status": "started", "boa": boa_key}


@app.delete("/rimuoviboa")
def rimuovi_boa(ip: str, port: int):
    boa_key = f"{ip}:{port}"
    with active_boes_lock:
        entry = active_boes.get(boa_key)
        if not entry:
            raise HTTPException(404, "Boa non trovata")

        t, stop_event = entry
        stop_event.set()
        t.join(timeout=5)
        active_boes.pop(boa_key, None)
    logger.info(f"[{boa_key}] Thread fermato da API")
    return {"status": "stopped", "boa": boa_key}


class StatoEnum(str, Enum):
    hold = "hold"
    loiter = "loiter"

class CambiaStatoRequest(BaseModel):
    ip: str
    port: int
    stato: StatoEnum

# Mapping modalità ArduPilot Copter
ARDUPILOT_MODE_MAP = {
    "hold": 0,    # HOLD (corretto: 0 per ArduCopter)
    "loiter": 5, # LOITER
}

def set_boa_state(ip, port, stato: StatoEnum):
    mode_id = ARDUPILOT_MODE_MAP[stato.value]
    logger.info(f"[set_boa_state] Cambio modalità richiesta: {stato.value} (custom_mode={mode_id}) su {ip}:{port}")
    # Connessione MAVLink
    mav = mavutil.mavlink_connection(
        f"udpout:{ip}:{port}",
        dialect="ardupilotmega",
        source_system=255,
        autoreconnect=True,
        timeout=2,
    )
    # Ping iniziale per aprire canale
    for _ in range(3):
        mav.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                               mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                               0, 0, 0)
        time.sleep(0.2)
    mav.wait_heartbeat(timeout=10)
    # Invia comando SET_MODE
    mav.set_mode(mode_id)
    logger.info(f"[set_boa_state] Comando set_mode({mode_id}) inviato, attendo conferma via heartbeat...")
    # Attendi conferma modalità
    deadline = time.time() + 10  # timeout aumentato
    ok = False
    while time.time() < deadline:
        msg = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if msg and hasattr(msg, "custom_mode"):
            logger.info(f"[set_boa_state] Ricevuto heartbeat: custom_mode={msg.custom_mode}")
            if msg.custom_mode == mode_id:
                ok = True
                logger.info(f"[set_boa_state] Cambio modalità confermato da heartbeat!")
                break
    mav.close()
    if not ok:
        logger.warning(f"[set_boa_state] Cambio modalità NON confermato entro il timeout")
        raise RuntimeError(f"Cambio modalità a {stato.value} non confermato")

@app.post("/cambia_stato")
def cambia_stato(req: CambiaStatoRequest):
    try:
        set_boa_state(req.ip, req.port, req.stato)
        return {"status": "ok", "stato": req.stato}
    except Exception as e:
        logger.error(f"Errore nel cambiare stato: {e}")
        raise HTTPException(500, f"Errore nel cambiare stato: {e}")

class VaiaRequest(BaseModel):
    ip: str
    port: int
    lat: float
    lon: float
    alt: float = 10.0

class VaiaStopRequest(BaseModel):
    ip: str
    port: int

GUIDED_MODE_ID = 15          # custom_mode GUIDED (ArduRover/Boat)
LOITER_MODE_ID = 5           # LOITER su Rover/Boat
DO_REPOSITION  = 192
BITMASK_LLA    = 1           # param2: bit0 -> use lat/lon/alt
ARM_MAGIC      = 21196       # param2 nel comando ARM per bypass safety

def wait_mode(mav, mode_id, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        hb = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if hb and hb.custom_mode == mode_id:
            return True
    return False

def wait_arm(mav, want_arm, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        mav.recv_match(blocking=False)
        if mav.motors_armed() == want_arm:
            return True
        time.sleep(0.3)
    return False

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = map(math.radians, (lat1, lat2))
    dphi  = math.radians(lat2 - lat1)
    dlamb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlamb/2)**2
    return 2*R*math.atan2(math.sqrt(a), math.sqrt(1-a))

def open_mavlink(ip, port):
    mav = mavutil.mavlink_connection(f"udpout:{ip}:{port}",
                                     dialect="ardupilotmega",
                                     source_system=255,
                                     autoreconnect=True,
                                     timeout=2)
    for _ in range(3):
        mav.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                               mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
        time.sleep(0.2)
    # Ricevi e logga il primo heartbeat
    msg = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=10)
    logger.info(f"[vaia] Heartbeat: {msg}")
    logger.info(f"[vaia] SYSID ricevuto: {mav.target_system}, COMPID ricevuto: {mav.target_component}")
    # Se SYSID resta 0, forzalo a 21
    if mav.target_system == 0:
        mav.target_system = 21
        logger.info("[vaia] Forzato SYSID a 21")
    # Forza COMPID a 0
    mav.target_component = 0
    logger.info(f"[vaia] SYSID usato: {mav.target_system}, COMPID usato: {mav.target_component}")
    return mav

def set_mode(mav, mode_id, label):
    logger.info(f"[vaia] set_mode → {label} (id={mode_id})")
    mav.set_mode(mode_id)
    if wait_mode(mav, mode_id):
        logger.info(f"[vaia] Mode {label} confermato.")
        return True
    logger.warning(f"[vaia] Mode {label} NON confermato!")
    return False

def arm(mav, timeout=8):
    logger.info("[vaia] ARM (force)")
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        1,           # param1 = 1 → arm
        ARM_MAGIC,   # param2 = 21196 bypass check
        0,0,0,0,0)

    end = time.time() + timeout
    ack_seen = False
    while time.time() < end:
        msg = mav.recv_match(blocking=True, timeout=1)
        if not msg:
            continue

        if msg.get_type() == "COMMAND_ACK" and \
           msg.command == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
            desc = mavutil.mavlink.enums['MAV_RESULT'][msg.result].description
            logger.info(f"[vaia] ARM ACK: {desc}")
            ack_seen = True

        if msg.get_type() == "STATUSTEXT":
            logger.info(f"[vaia] STATUSTEXT: {msg.text}")

        if mav.motors_armed():
            logger.info("[vaia] ARM confermato.")
            return True

    logger.warning("[vaia] ARM NON confermato!")
    if not ack_seen:
        logger.warning("[vaia] (nessun COMMAND_ACK ricevuto)")
    return False

def disarm(mav):
    logger.info("[vaia] DISARM")
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 0, 0,0,0,0,0,0)
    wait_arm(mav, False)

def do_reposition(mav, lat, lon, alt):
    # Primo tentativo: ground speed 1.0
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        DO_REPOSITION, 0,
        1.0,               # ground speed > 0
        BITMASK_LLA,       # bitmask: use position
        0, 0,              # reserved, yaw
        float(lat), float(lon), float(alt))
    logger.info("[vaia] DO_REPOSITION inviato (ground speed 1.0)")
    # Secondo tentativo: ground speed 2.0 (alcuni firmware richiedono valori diversi)
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        DO_REPOSITION, 0,
        2.0,               # ground speed > 0
        BITMASK_LLA,       # bitmask: use position
        0, 0,              # reserved, yaw
        float(lat), float(lon), float(alt))
    logger.info("[vaia] DO_REPOSITION inviato (ground speed 2.0)")

def set_position_target_global_int(mav, lat, lon, alt):
    # lat/lon in 1E7, alt in metri
    time_boot_ms = int((time.time() * 1e3) % 4294967295)
    mav.mav.set_position_target_global_int_send(
        time_boot_ms,
        mav.target_system,
        mav.target_component,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        0b0000111111111000,  # type_mask: ignora velocità, accel, yaw
        int(lat * 1e7),
        int(lon * 1e7),
        float(alt),
        0, 0, 0,  # vx, vy, vz
        0, 0, 0,  # afx, afy, afz
        0, 0      # yaw, yaw_rate
    )
    logger.info("[vaia] SET_POSITION_TARGET_GLOBAL_INT inviato")

def vaia_thread(ip, port, lat, lon, alt, stop_event):
    boa_key = f"{ip}:{port}"
    try:
        mav = open_mavlink(ip, port)
    except Exception as e:
        logger.error(f"[vaia] {boa_key}: Connessione fallita: {e}")
        return

    try:
        # 1. GUIDED + ARM
        if not set_mode(mav, GUIDED_MODE_ID, "GUIDED") or not arm(mav):
            logger.error(f"[vaia] {boa_key}: impossibile entrare in GUIDED armato.")
            mav.close()
            return

        logger.info(f"[vaia] Target richiesto: lat={lat}, lon={lon}, alt={alt}")
        start = time.time()
        last_tx = 0
        arrived = False

        while time.time() - start < VAI_A_TIMEOUT_SEC:
            if stop_event.is_set():
                logger.info(f"[vaia] Stop richiesto per {boa_key}")
                break

            now = time.time()
            if now - last_tx > VAI_A_RETRY_SEC:
                logger.info(f"[vaia] Invio DO_REPOSITION a lat={lat}, lon={lon}, alt={alt}")
                do_reposition(mav, lat, lon, alt)
                set_position_target_global_int(mav, lat, lon, alt)
                last_tx = now

            msg = mav.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=1)
            if msg:
                clat, clon = msg.lat / 1e7, msg.lon / 1e7
                logger.info(f"[vaia] Posizione attuale: lat={clat}, lon={clon}")
                dist = haversine(clat, clon, lat, lon)
                logger.info(f"[vaia] Distanza corrente dal target: {dist:.1f} m (target: {lat},{lon})")
                if dist < VAI_A_TOLERANCE_METERS:
                    arrived = True
                    break

        # 2. Torna in LOITER, disarma
        set_mode(mav, LOITER_MODE_ID, "LOITER")
        disarm(mav)

        if arrived:
            logger.info(f"[vaia] {boa_key}: Arrivata al punto.")
        else:
            logger.warning(f"[vaia] {boa_key}: Non arrivata (timeout/stop).")
    finally:
        mav.close()

@app.post("/vaia")
def vaia(req: VaiaRequest):
    boa_key = f"{req.ip}:{req.port}"
    with active_vaia_lock:
        if boa_key in active_vaia:
            raise HTTPException(400, "Vaia già attivo per questa boa")
        stop_event = threading.Event()
        t = threading.Thread(
            target=vaia_thread,
            name=f"vaia-{boa_key}",
            args=(req.ip, req.port, req.lat, req.lon, req.alt, stop_event),
            daemon=True,
        )
        t.start()
        active_vaia[boa_key] = (t, stop_event)
    return {"status": "started", "boa": boa_key}

@app.post("/stop_vaia")
def stop_vaia(req: VaiaStopRequest):
    boa_key = f"{req.ip}:{req.port}"
    with active_vaia_lock:
        entry = active_vaia.get(boa_key)
        if not entry:
            raise HTTPException(404, "Nessun vaia attivo per questa boa")
        t, stop_event = entry
        stop_event.set()
        t.join(timeout=5)
        active_vaia.pop(boa_key, None)
    return {"status": "stopped", "boa": boa_key}

def publish_isonline(ip, port, is_online):
    """
    Pubblica lo stato isonline (true/false) su MQTT per la boa specificata.
    """
    topic_base = MQTT_TOPIC_FMT.format(f"{ip.replace('.', '_')}_{port}")
    isonline_topic = f"{topic_base}/isonline"
    client = mqtt.Client(client_id=f"isonline_{ip}_{port}", clean_session=True)
    try:
        client.connect("localhost", MQTT_PORT, keepalive=5)
        client.loop_start()
        client.publish(isonline_topic, str(is_online).lower(), qos=MQTT_QOS, retain=True)
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        logger.warning(f"[MQTT] Errore pubblicando isonline={is_online} per {ip}:{port}: {e}")
