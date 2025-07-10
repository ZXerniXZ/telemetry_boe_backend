import threading
from pymavlink import mavutil
from paho.mqtt import client as mqtt
import json
import time
import logging
from colorama import Fore, Style, init
import os

# Inizializza colorama
def get_env_var(name, default, cast=str):
    value = os.environ.get(name, default)
    try:
        return cast(value)
    except Exception:
        return default

init(autoreset=True)

############ Configurazione tramite variabili d'ambiente #################
mavlink_mode = get_env_var("MAVLINK_MODE", "udpin")  # udpin o udpout
mavlinkPort = get_env_var("MAVLINK_PORT", 14550, int)
mavlinkIp = get_env_var("MAVLINK_IP", "0.0.0.0")

mqttIp = get_env_var("MQTT_IP", "localhost")
mqttPort = get_env_var("MQTT_PORT", 1883, int)
tickAlive = get_env_var("TICK_ALIVE", 60, int)

log_level = get_env_var("LOG_LEVEL", "INFO").upper()
timeout_seconds = get_env_var("TIMEOUT_SECONDS", 10, int)
##################################################

# Configurazione logging
logging.basicConfig(level=getattr(logging, log_level, logging.INFO),
                    format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# Inizializzazione della lista e del dizionario
connection_status = False

def dict_conversion_to_json(dict_data):
    """Converte un dizionario in una stringa JSON formattata."""
    return json.dumps(dict_data, indent=4)

def json_push_to_file(file_name, telemetry):
    """Scrive una stringa JSON su un file."""
    with open(str(file_name), "w") as file:
        file.write(telemetry)

def update_data_sort_for_dict(dict_data, the_connection):
    """Aggiorna il dizionario con i dati di telemetria ricevuti e lo converte in JSON."""
    for i in range(28):
        telemetry_data = the_connection.recv_match(blocking=True)
        if telemetry_data:
            msg_type = telemetry_data.get_type()
            dict_data[msg_type] = {field: getattr(telemetry_data, field) for field in telemetry_data.fieldnames}
    telemetry_json = dict_conversion_to_json(dict_data)
    # Salva i dati in un file separato per ogni IP
    json_push_to_file("dati_local.json", telemetry_json)
    return telemetry_json

def push_json_to_mqtt(telemetry, mqtt_path, client):
    """Pubblica una stringa JSON su un topic MQTT."""
    client.publish(mqtt_path, str(telemetry))

def on_connect(client, userdata, flags, rc):
    """Callback per la connessione al broker MQTT."""
    logger.info(Fore.GREEN + f"Connesso con codice: {rc}")
    client.subscribe("heartBeat/redNodeBeat")

def on_message(client, userdata, msg, the_connection, main_dictionary, TOPIC_FOR_JSON, stop_event):
    """Callback per la ricezione di un messaggio su un topic MQTT."""
    json_data = update_data_sort_for_dict(main_dictionary, the_connection)
    push_json_to_mqtt(json_data, TOPIC_FOR_JSON, client)
    if msg.payload.decode("utf-8") == "terminate":
        stop_event.set()
        logger.warning(Fore.YELLOW + "Ricevuto messaggio di terminazione da host remoto, chiusura in corso...")

def wait_heartbeat_with_timeout(the_connection, timeout):
    """Attende il primo heartbeat con un timeout."""
    start_time = time.time()
    while True:
        if time.time() - start_time > timeout:
            raise TimeoutError("Timeout waiting for heartbeat")
        heartbeat = the_connection.recv_match(type='HEARTBEAT', blocking=False)
        if heartbeat:
            return heartbeat
        time.sleep(0.1)

def mavlink_thread(stop_event):
    """Thread principale per la gestione della connessione MAVLink e MQTT."""
    mavlink_link = f"udpin:{mavlinkIp}:{mavlinkPort}"
    the_connection = mavutil.mavlink_connection(mavlink_link)
    main_dictionary = {}
    client = mqtt.Client()
    try:
        client.connect(mqttIp, mqttPort, tickAlive)
        the_connection.mav.ping_send(int(time.time()), 0, 0, 0)
        try:
            wait_heartbeat_with_timeout(the_connection, timeout_seconds)
            global connection_status
            connection_status = True
        except TimeoutError as e:
            logger.error(Fore.RED + f"Errore nella connessione MAVLink: {e}")
            connection_status = False
            stop_event.set()
            return
        TOPIC_FOR_JSON = f"mavlink_data_local/json"
        client.on_connect = on_connect
        client.on_message = lambda client, userdata, msg: on_message(client, userdata, msg, the_connection, main_dictionary, TOPIC_FOR_JSON, stop_event)
        client.loop_start()
        logger.info(f"Connessione MAVLink in ascolto su {mavlinkIp}:{mavlinkPort}")
        while not stop_event.is_set():
            time.sleep(1)
    except Exception as e:
        logger.error(Fore.RED + f"Errore nella connessione MAVLink: {e}")
        connection_status = False
    finally:
        client.loop_stop()
        client.disconnect()
        stop_event.set()

def mqtt_listener_thread(stop_event):
    """Thread per ascoltare i messaggi MQTT sul topic specificato."""
    def on_connect(client, userdata, flags, rc):
        logger.info(Fore.GREEN + f"MQTT listener connesso con codice: {rc}")
        client.subscribe("heartBeat/redNodeBeat")
    def on_message(client, userdata, msg):
        message = msg.payload.decode("utf-8")
        if message == "terminate":
            logger.warning(Fore.YELLOW + "Ricevuto messaggio di terminazione da host remoto, chiusura in corso...")
            stop_event.set()
            os._exit(0)
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(mqttIp, mqttPort, tickAlive)
    client.loop_start()
    stop_event.wait()
    client.loop_stop()
    client.disconnect()

def main():
    """Funzione principale per avviare i thread e attendere lo stop event."""
    threads = []
    stop_events = []
    stop_event = threading.Event()
    t = threading.Thread(target=mavlink_thread, args=(stop_event,))
    t.start()
    threads.append(t)
    stop_events.append(stop_event)
    mqtt_stop_event = threading.Event()
    mqtt_thread = threading.Thread(target=mqtt_listener_thread, args=(mqtt_stop_event,))
    mqtt_thread.start()
    threads.append(mqtt_thread)
    for t in threads:
        t.join()
    logger.info(Fore.GREEN + "Servizio terminato correttamente.")
    print(Fore.WHITE + "")

if __name__ == "__main__":
    main()
