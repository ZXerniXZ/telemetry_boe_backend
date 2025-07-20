# telemetry_backend.py
# ==============================================================
import os
import threading
import time
import atexit
import nmap
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from paho.mqtt import client as mqtt
from pymavlink import mavutil

# ---- fetch_boe import ------------------------------------------------
from fetch_boe.shared import (
    stop_event, MQTT_PORT, MQTT_TOPIC_FMT, MQTT_QOS, MQTT_RETAIN, LOG_LEVEL, PUBLISH_INTERVAL, HEARTBEAT_HZ, HEARTBEAT_PERIOD, DEVICE_TIMEOUT
)

import os
from dotenv import load_dotenv
load_dotenv()

# ---- Config ----------------------------------------------------------
SCAN_SUBNET     = os.environ.get("SCAN_SUBNET", "10.8.0.0/24")
MIN_LAST_OCTET  = int(os.environ.get("MIN_LAST_OCTET", "30"))

serializer_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="json")

# active_boes: {boa_key: (thread, stop_event)}
active_boes: Dict[str, Tuple[threading.Thread, threading.Event]] = {}
active_boes_lock = threading.Lock()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("telemetry_backend")

# ----------------------------------------------------------------------
# FastAPI app + CORS
# ----------------------------------------------------------------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# Helper MAVLink per scrivere SYSID_THISMAV
# ----------------------------------------------------------------------
def _pid_to_str(pid):
    return pid.decode(errors="ignore").rstrip("\x00") if isinstance(pid, (bytes, bytearray)) else str(pid).rstrip("\x00")


def _name_to_pid(name: str):
    return name.encode()[:16]


def _set_param(ip: str, port: int, name: str, value: float, timeout: float = 5.0):
    """Connessione breve -> PARAM_SET -> conferma."""
    mav = mavutil.mavlink_connection(f"udpout:{ip}:{port}",
                                     dialect="ardupilotmega",
                                     source_system=255,
                                     autoreconnect=True,
                                     timeout=2)

    for _ in range(3):  # ping per aprire ritorno
        mav.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                               mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                               0, 0, 0)
        time.sleep(0.2)

    mav.wait_heartbeat(timeout=10)

    ptype = mavutil.mavlink.MAV_PARAM_TYPE_REAL32
    mav.mav.param_set_send(mav.target_system, mav.target_component,
                           _name_to_pid(name), float(value), ptype)

    deadline = time.time() + timeout
    ok = False
    while time.time() < deadline:
        msg = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
        if msg and _pid_to_str(msg.param_id) == name:
            ok = abs(msg.param_value - float(value)) < 1e-4
            break
    mav.close()
    if not ok:
        raise RuntimeError(f"Set {name}={value} non confermato")

# ----------------------------------------------------------------------
# Thread wrapper con switch SYSID
# ----------------------------------------------------------------------
def thread_wrapper(ip: str, port: int, pool, stop_event: threading.Event):
    boa_key = f"{ip}:{port}"
    try:
        logger.info(f"[{boa_key}] SYSID_THISMAV -> 21")
        _set_param(ip, port, "SYSID_THISMAV", 21)
    except Exception as e:
        logger.error(f"[{boa_key}] Set SYSID 21 fallito: {e}")

    try:
        from fetch_boe.fetch_script import handle_device
        handle_device(ip, port, pool, stop_event=stop_event)
    finally:
        try:
            logger.info(f"[{boa_key}] SYSID_THISMAV -> 1")
            _set_param(ip, port, "SYSID_THISMAV", 1)
        except Exception as e:
            logger.warning(f"[{boa_key}] Ripristino SYSID fallito: {e}")

        with active_boes_lock:
            active_boes.pop(boa_key, None)
        logger.info(f"[{boa_key}] Thread terminato.")

# ----------------------------------------------------------------------
# Funzione per marcare offline tutte le boe su MQTT
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

# ----------------------------------------------------------------------
# Shutdown handler FastAPI (graceful CTRL+C / SIGTERM)
# ----------------------------------------------------------------------
@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutdown event: fermo i thread attivi…")
    with active_boes_lock:
        for boa_key, (t, stop_ev) in list(active_boes.items()):
            stop_ev.set()
            t.join(timeout=5)
            active_boes.pop(boa_key, None)
    set_all_boes_offline()
    logger.info("Backend chiuso pulitamente.")

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
            if int(host.split('.')[-1]) > MIN_LAST_OCTET:
                found.append(host)
        except ValueError:
            continue
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
            daemon=True
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


def handle_device(ip: str, port: int, pool: ThreadPoolExecutor, stop_event=None) -> None:
    import base64
    import json
    if stop_event is None:
        from fetch_boe.shared import stop_event as global_stop_event
        stop_event = global_stop_event
    client_id = f"telemetry_{ip.replace('.', '_')}_{port}"
    topic_base = MQTT_TOPIC_FMT.format(f"{ip.replace('.', '_')}_{port}")
    isonline_topic = f"{topic_base}/isonline"
    client = mqtt.Client(client_id=client_id, clean_session=False)
    client.enable_logger(logger)
    try:
        client.connect("localhost", MQTT_PORT, keepalive=30)
    except Exception as exc:
        logger.error(f"[{ip}:{port}] MQTT connect failed: {exc}")
    client.loop_start()
    last_pub = 0.0
    reconnect_attempts = 0
    max_reconnect_attempts = 3
    backoff = 1.0
    def _bytes_to_b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).decode().rstrip("=")
    def _json_safe(v):
        if isinstance(v, (bytes, bytearray)):
            return _bytes_to_b64(v)
        if isinstance(v, (list, tuple)):
            return [_json_safe(i) for i in v]
        try:
            return int(v)
        except (TypeError, ValueError):
            return v
    def mav_msg_to_payload(msg):
        return json.dumps({
            "timestamp": time.time(),
            "type": msg.get_type(),
            "data": {k: _json_safe(v) for k, v in msg.to_dict().items()},
        }, separators=(",", ":"))
    while not stop_event.is_set():
        try:
            logger.info(f"[{ip}:{port}] Opening UDP-OUT…")
            mav = mavutil.mavlink_connection(
                f"udpout:{ip}:{port}",
                dialect="ardupilotmega",
                source_system=255,
                autoreconnect=True,
                timeout=2,
            )
            for _ in range(3):
                mav.mav.heartbeat_send(6, 8, 0, 0, 3)
                time.sleep(0.2)
            if not mav.wait_heartbeat(timeout=10):
                raise TimeoutError("No heartbeat within 10 s")
            client.publish(isonline_topic, "true", qos=MQTT_QOS, retain=True)
            logger.info(f"[{ip}:{port}] Heartbeat OK – streaming…")
            backoff = 1.0
            reconnect_attempts = 0
            last_hb = time.time()
            last_msg_time = time.time()
            while not stop_event.is_set():
                now = time.time()
                if now - last_hb >= HEARTBEAT_PERIOD:
                    try:
                        mav.mav.heartbeat_send(6, 8, 0, 0, 3)
                        last_hb = now
                    except Exception as exc:
                        logger.warning(f"[{ip}:{port}] Heartbeat send error: {exc}")
                msg = mav.recv_match(blocking=True, timeout=0.2)
                if msg is not None:
                    last_msg_time = now
                if now - last_msg_time > DEVICE_TIMEOUT:
                    logger.warning(f"[{ip}:{port}] No MAVLink data for {DEVICE_TIMEOUT:.1f} s, assuming disconnected")
                    break
                if msg is None:
                    continue
                if now - last_pub < PUBLISH_INTERVAL:
                    continue
                future = pool.submit(mav_msg_to_payload, msg)
                try:
                    payload = future.result(timeout=0.3)
                except Exception as exc:
                    logger.debug(f"[{ip}:{port}] Serialisation failed: {exc}")
                    continue
                try:
                    msg_type = msg.get_type()
                    dynamic_topic = f"{topic_base}/{msg_type}"
                    client.publish(dynamic_topic, payload, qos=MQTT_QOS, retain=MQTT_RETAIN)
                    last_pub = now
                except Exception as exc:
                    logger.error(f"[{ip}:{port}] MQTT publish failed: {exc}")
        except Exception as exc:
            logger.error(f"[{ip}:{port}] Connection error: {exc}")
            reconnect_attempts += 1
        finally:
            try:
                client.publish(isonline_topic, "false", qos=MQTT_QOS, retain=True)
            except Exception:
                pass
            try:
                mav.close()  # type: ignore[name-defined]
            except Exception:
                pass
        if stop_event.is_set():
            break
        if reconnect_attempts >= max_reconnect_attempts:
            logger.error(f"[{ip}:{port}] Max reconnect attempts reached ({max_reconnect_attempts}). Stopping thread.")
            break
        logger.info(f"[{ip}:{port}] Reconnecting in {backoff:.1f} s… (attempt {reconnect_attempts}/{max_reconnect_attempts})")
        stop_event.wait(backoff)
        backoff = min(backoff * 2, 60)
    try:
        client.loop_stop()
        client.disconnect()
    except Exception:
        pass
    logger.info(f"[{ip}:{port}] Thread exited.")
