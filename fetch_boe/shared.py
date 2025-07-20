import os
import threading

stop_event = threading.Event()

MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC_FMT = os.environ.get("MQTT_TOPIC_FMT", "mavlink/{}/json")
MQTT_QOS = int(os.environ.get("MQTT_QOS", "0"))
MQTT_RETAIN = os.environ.get("MQTT_RETAIN", "false").lower() in ("true", "1", "yes")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
PUBLISH_INTERVAL = float(os.environ.get("PUBLISH_INTERVAL", "3"))
HEARTBEAT_HZ = float(os.environ.get("HEARTBEAT_HZ", "1"))
HEARTBEAT_PERIOD = 1.0 / max(0.1, HEARTBEAT_HZ)
DEVICE_TIMEOUT = float(os.environ.get("DEVICE_TIMEOUT", "10"))
VAI_A_RETRY_SEC = float(os.environ.get("VAI_A_RETRY_SEC", "3"))
VAI_A_TOLERANCE_METERS = float(os.environ.get("VAI_A_TOLERANCE_METERS", "2"))
VAI_A_TIMEOUT_SEC = float(os.environ.get("VAI_A_TIMEOUT_SEC", "60")) 