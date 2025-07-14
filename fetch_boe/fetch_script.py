"""fetch_boe_threaded.py

Enhanced version of the original telemetry fetcher.
Supports up to ~10 concurrent devices handled in independent threads.
Main additions:
- Automatic reconnect loop with exponential back-off (Point 1)
- Deterministic, reusable MQTT client per device (Point 2)
- Still thread-based but uses a small ThreadPoolExecutor for JSON serialisation (Point 3)
- JSON-safe serialisation of MAVLink messages (Point 4)
- QoS/retain configurable through env vars (Point 5)
- Topic includes port to avoid collisions (Point 6)
- Structured logging with timestamps and severity (Point 7)
- Graceful shutdown on SIGINT/SIGTERM (Point 8)
- **Continuous heartbeat (≈ 1 Hz) to keep the vehicle streaming**  (NEW)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Tuple

from paho.mqtt import client as mqtt
from pymavlink import mavutil

# ---------------------------------------------------------------------------
# Load .env if present -------------------------------------------------------
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass  # facoltativo

# ---------------------------------------------------------------------------
# Environment configuration --------------------------------------------------
DEVICES_STR       = os.environ.get("DEVICES", "")
MQTT_IP           = os.environ.get("MQTT_IP", "")
MQTT_PORT_STR     = os.environ.get("MQTT_PORT", "1883")
MQTT_TOPIC_FMT    = os.environ.get("MQTT_TOPIC_FMT", "mavlink/{}/json")
MQTT_QOS_STR      = os.environ.get("MQTT_QOS", "0")
MQTT_RETAIN_STR   = os.environ.get("MQTT_RETAIN", "false").lower()
LOG_LEVEL         = os.environ.get("LOG_LEVEL", "INFO").upper()
PUBLISH_INTERVAL  = float(os.environ.get("PUBLISH_INTERVAL", "3"))
HEARTBEAT_HZ      = float(os.environ.get("HEARTBEAT_HZ", "1"))      # ### NEW
HEARTBEAT_PERIOD  = 1.0 / max(0.1, HEARTBEAT_HZ)                    # ### NEW

# ---------------------------------------------------------------------------
# Validation ----------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

errors: list[str] = []
if not DEVICES_STR:
    errors.append("DEVICES non impostata – es: DEVICES=10.8.0.52:14550,10.8.0.55:14550")
if not MQTT_IP:
    errors.append("MQTT_IP non impostata – es: MQTT_IP=localhost")

try:
    MQTT_PORT = int(MQTT_PORT_STR)
except ValueError:
    errors.append("MQTT_PORT deve essere un intero (es: 1883)")

try:
    MQTT_QOS = int(MQTT_QOS_STR)
    if MQTT_QOS not in (0, 1, 2):
        raise ValueError
except ValueError:
    errors.append("MQTT_QOS deve essere 0, 1 o 2")

MQTT_RETAIN = MQTT_RETAIN_STR in ("true", "1", "yes")

if errors:
    for e in errors:
        logger.error(e)
    raise SystemExit(1)

# ---------------------------------------------------------------------------
# Parse devices -------------------------------------------------------------
DEVICES: list[Tuple[str, int]] = []
for entry in DEVICES_STR.split(","):
    entry = entry.strip()
    if not entry:
        continue
    if ":" not in entry:
        logger.error("Formato dispositivo non valido: '%s' (usa IP:PORT)", entry)
        raise SystemExit(1)
    ip, port_str = entry.split(":", 1)
    try:
        port = int(port_str)
    except ValueError:
        logger.error("Porta non valida per '%s'", entry)
        raise SystemExit(1)
    DEVICES.append((ip.strip(), port))

# ---------------------------------------------------------------------------
# Graceful shutdown flag ----------------------------------------------------
stop_event = threading.Event()
signal.signal(signal.SIGINT,  lambda s, f: (logger.info("SIGINT"),  stop_event.set()))
signal.signal(signal.SIGTERM, lambda s, f: (logger.info("SIGTERM"), stop_event.set()))

# ---------------------------------------------------------------------------
# Helpers -------------------------------------------------------------------
def _bytes_to_b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

def _json_safe(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray)):
        return _bytes_to_b64(v)
    if isinstance(v, (list, tuple)):
        return [_json_safe(i) for i in v]
    try:
        return int(v)  # Enum → int
    except (TypeError, ValueError):
        return v

def mav_msg_to_payload(msg: mavutil.mavlink.MAVLink_message) -> str:
    return json.dumps(
        {
            "timestamp": time.time(),
            "type": msg.get_type(),
            "data": {k: _json_safe(v) for k, v in msg.to_dict().items()},
        },
        separators=(",", ":"),
    )

# ---------------------------------------------------------------------------
# Per-device thread ---------------------------------------------------------
def handle_device(ip: str, port: int, pool: ThreadPoolExecutor) -> None:
    client_id = f"telemetry_{ip.replace('.', '_')}_{port}"
    topic     = MQTT_TOPIC_FMT.format(f"{ip.replace('.', '_')}_{port}")

    client = mqtt.Client(client_id=client_id, clean_session=False)
    client.enable_logger(logger)

    try:
        client.connect(MQTT_IP, MQTT_PORT, keepalive=30)
    except Exception as exc:
        logger.error("[%s:%s] MQTT connect failed: %s", ip, port, exc)

    client.loop_start()

    backoff = 1.0
    last_pub = 0.0

    while not stop_event.is_set():
        try:
            logger.info("[%s:%s] Opening UDP-OUT…", ip, port)
            mav = mavutil.mavlink_connection(
                f"udpout:{ip}:{port}",
                dialect="ardupilotmega",
                source_system=255,
                autoreconnect=True,
                timeout=2,                     # ### CHANGED (was 5) – keep loop responsive
            )

            # prime routing table
            for _ in range(3):
                mav.mav.heartbeat_send(6, 8, 0, 0, 3)
                time.sleep(0.2)

            if not mav.wait_heartbeat(timeout=10):
                raise TimeoutError("No heartbeat within 10 s")

            logger.info("[%s:%s] Heartbeat OK – streaming…", ip, port)
            backoff = 1.0
            last_hb = time.time()            # ### NEW – track heartbeat sent

            while not stop_event.is_set():
                # ---------- periodic heartbeat --------------------------------
                now = time.time()
                if now - last_hb >= HEARTBEAT_PERIOD:
                    try:
                        mav.mav.heartbeat_send(6, 8, 0, 0, 3)
                        last_hb = now
                    except Exception as exc:
                        logger.warning("[%s:%s] Heartbeat send error: %s", ip, port, exc)

                # ---------- recv telemetry ------------------------------------
                msg = mav.recv_match(blocking=True, timeout=0.2)
                if msg is None:
                    continue
                if now - last_pub < PUBLISH_INTERVAL:
                    continue
                future = pool.submit(mav_msg_to_payload, msg)
                try:
                    payload = future.result(timeout=0.3)
                except Exception as exc:
                    logger.debug("[%s:%s] Serialisation failed: %s", ip, port, exc)
                    continue

                # --- Pubblica su topic dinamico per type ---
                try:
                    msg_type = msg.get_type()
                    dynamic_topic = f"{MQTT_TOPIC_FMT.format(f'{ip.replace('.', '_')}_{port}')}/{msg_type}"
                    client.publish(dynamic_topic, payload, qos=MQTT_QOS, retain=MQTT_RETAIN)
                    last_pub = now
                except Exception as exc:
                    logger.error("[%s:%s] MQTT publish failed: %s", ip, port, exc)

        except Exception as exc:
            logger.error("[%s:%s] Connection error: %s", ip, port, exc)
        finally:
            try:
                mav.close()  # type: ignore[name-defined]
            except Exception:
                pass

        if stop_event.is_set():
            break
        logger.info("[%s:%s] Reconnecting in %.1f s…", ip, port, backoff)
        stop_event.wait(backoff)
        backoff = min(backoff * 2, 60)

    try:
        client.loop_stop()
        client.disconnect()
    except Exception:
        pass
    logger.info("[%s:%s] Thread exited.", ip, port)

# ---------------------------------------------------------------------------
def main() -> None:
    logger.info("Monitoring %d device(s)…", len(DEVICES))
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="json") as pool:
        threads = [
            threading.Thread(
                target=handle_device,
                name=f"dev-{ip}:{port}",
                args=(ip, port, pool),
                daemon=False,
            )
            for ip, port in DEVICES
        ]
        for t in threads:
            t.start()

        while not stop_event.is_set():
            stop_event.wait(1)

        logger.info("Shutdown requested – waiting for threads…")
        for t in threads:
            t.join()
    logger.info("Bye.")

if __name__ == "__main__":
    main()
