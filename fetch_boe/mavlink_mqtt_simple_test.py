import time, json, threading, os
from pymavlink import mavutil
from paho.mqtt import client as mqtt

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[ERRORE] problema durante il caricamendo del file env")# dotenv non installato, ignora

# --- impostazioni da .env o default -----------------------------------------
DEVICES_STR = os.environ.get("DEVICES", "")
MQTT_IP = os.environ.get("MQTT_IP", "")
MQTT_PORT = os.environ.get("MQTT_PORT", "")
MQTT_TOPIC_FMT = os.environ.get("MQTT_TOPIC_FMT", "mavlink/{}/json")

# Controllo parametri obbligatori
error = False
if not DEVICES_STR:
    print("[ERRORE] La variabile DEVICES non è impostata. Inseriscila nel file .env (es: DEVICES=10.8.0.52:14550,10.8.0.55:14550)")
    error = True
if not MQTT_IP:
    print("[ERRORE] La variabile MQTT_IP non è impostata. Inseriscila nel file .env (es: MQTT_IP=localhost)")
    error = True
try:
    MQTT_PORT = int(MQTT_PORT)
except Exception:
    print("[ERRORE] La variabile MQTT_PORT non è un intero valido. Inseriscila nel file .env (es: MQTT_PORT=1883)")
    error = True
if error:
    exit(1)

# Parsing della lista dispositivi
DEVICES = []
for entry in DEVICES_STR.split(","):
    entry = entry.strip()
    if not entry:
        continue
    if ":" in entry:
        ip, port = entry.split(":")
        DEVICES.append((ip.strip(), int(port.strip())))
    else:
        print(f"[ERRORE] Formato dispositivo non valido: '{entry}'. Usa IP:PORT.")
        exit(1)
# ---------------------------------------------------------------------------

def handle_device(ip, port):
    try:
        print(f"[{ip}] Apro connessione UDP-OUT ...")
        mav = mavutil.mavlink_connection(
            f"udpout:{ip}:{port}",
            dialect="ardupilotmega",
            source_system=255,
            autoreconnect=True,
            timeout=5
        )

        for _ in range(3):
            mav.mav.heartbeat_send(6, 8, 0, 0, 3)
            time.sleep(0.3)

        if not mav.wait_heartbeat(timeout=10):
            print(f"[{ip}] Nessun heartbeat entro 10 s — controlla routing/NAT o la config del veicolo.")
            return
        print(f"[{ip}] Heartbeat OK.")

        client = mqtt.Client()
        client.connect(MQTT_IP, MQTT_PORT, keepalive=30)
        client.loop_start()
        topic = MQTT_TOPIC_FMT.format(ip.replace(".", "_"))

        while True:
            msg = mav.recv_match(blocking=True, timeout=2)
            if msg:
                try:
                    payload = json.dumps({
                        "timestamp": time.time(),
                        "type": msg.get_type(),
                        "data": msg.to_dict()
                    })
                    client.publish(topic, payload)
                except TypeError as e:
                    print(f"[{ip}] Errore: {e}")
                    continue  # passa alla prossima iterazione
            else:
                if not mav.mav:
                    print(f"[{ip}] Connessione chiusa, mi riconnetto...")
                    break

    except Exception as e:
        print(f"[{ip}] Errore: {e}")

    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

# --- avvio un thread per ogni veicolo ----------------------------
threads = [
    threading.Thread(target=handle_device, args=(ip, port), daemon=True)
    for ip, port in DEVICES
]
for t in threads:
    t.start()
for t in threads:
    t.join()
