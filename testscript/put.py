#!/usr/bin/env python3
"""
sysid_switch_and_modes.py  (v2.0)
--------------------------------------------------------------
Sequenza richiesta:
  1. SYSID_THISMAV = 21
  2. Mode → LOITER
  3. Wait 5 s
  4. Mode → HOLD
  5. SYSID_THISMAV = 1
  6. Disconnect

Miglioramenti v2.0
------------------
* Dopo ogni richiesta di modalità:
    • aspetta che `custom_mode` cambi al valore atteso (≤ timeout)
    • se non cambia entro 2 s, ritenta con `COMMAND_LONG`
* Compatibile con autopiloti che **non** inviano COMMAND_ACK.
* Gestione param_id str/bytes robusta.
"""

import argparse
import sys
import time
from pymavlink import mavutil

# ----------------------------------------------------------------------
# param_id helpers
# ----------------------------------------------------------------------
def pid_to_str(pid):
    return pid.decode(errors="ignore").rstrip("\x00") if isinstance(pid, (bytes, bytearray)) else str(pid).rstrip("\x00")


def name_to_pid(name):
    return name.encode()[:16]

# ----------------------------------------------------------------------
# Connessione
# ----------------------------------------------------------------------
def connect(endpoint: str, is_udpout: bool):
    m = mavutil.mavlink_connection(endpoint,
                                   dialect="ardupilotmega",
                                   source_system=255,
                                   autoreconnect=True)

    if is_udpout:
        for _ in range(3):
            m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                                 mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                                 0, 0, 0)
            time.sleep(0.2)

    hb = m.wait_heartbeat(timeout=10)
    if not hb:
        sys.exit("Nessun heartbeat entro 10 s – abort.")
    m.target_component = hb.get_srcComponent()
    print(f"✓ Heartbeat iniziale da sys={m.target_system} comp={m.target_component}")
    return m

# ----------------------------------------------------------------------
# Helpers MAVLink
# ----------------------------------------------------------------------
def wait_for_sysid(master, desired, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        hb = master.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if hb and hb.get_srcSystem() == desired:
            master.target_system = desired
            master.target_component = hb.get_srcComponent()
            print(f"✓ Connesso ora a sys={desired} comp={master.target_component}")
            return True
    sys.exit(f"Timeout in attesa di sysid {desired}")


def get_param(master, name, timeout=5):
    master.mav.param_request_read_send(master.target_system,
                                       master.target_component,
                                       name_to_pid(name), -1)
    end = time.time() + timeout
    while time.time() < end:
        msg = master.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
        if msg and pid_to_str(msg.param_id) == name:
            return msg.param_value, msg.param_type
    raise TimeoutError(f"No PARAM_VALUE for {name}")


def set_param(master, name, value, ptype=None, timeout=5):
    if ptype is None:
        try:
            _, ptype = get_param(master, name)
        except TimeoutError:
            ptype = mavutil.mavlink.MAV_PARAM_TYPE_REAL32

    master.mav.param_set_send(master.target_system,
                              master.target_component,
                              name_to_pid(name),
                              float(value), ptype)

    end = time.time() + timeout
    while time.time() < end:
        msg = master.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
        if msg and pid_to_str(msg.param_id) == name:
            ok = abs(msg.param_value - float(value)) < 1e-4
            print(f"   {name} → {msg.param_value} ({'OK' if ok else 'mismatch'})")
            return ok
    raise TimeoutError("No PARAM_VALUE confirmation")


def wait_for_mode(master, mode_id, timeout=5):
    """Attende che custom_mode diventi mode_id entro timeout secondi."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hb = master.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if hb and hb.custom_mode == mode_id:
            return True
    return False


def set_mode(master, mode_name, total_timeout=5):
    """
    Prova prima con SET_MODE, se dopo 2 s il mode non cambia ritenta con COMMAND_LONG.
    """
    mapping = master.mode_mapping()
    if mode_name not in mapping:
        sys.exit(f"Modalità {mode_name} non supportata")
    mode_id = mapping[mode_name]

    # --- primo tentativo: SET_MODE ---
    master.mav.set_mode_send(master.target_system,
                             mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                             mode_id)
    if wait_for_mode(master, mode_id, timeout=2):
        print(f"   Mode → {mode_name} (via SET_MODE)")
        return

    # --- fallback: COMMAND_LONG ---
    master.mav.command_long_send(master.target_system,
                                 master.target_component,
                                 mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                                 0,
                                 mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                                 mode_id, 0, 0, 0, 0, 0)
    if wait_for_mode(master, mode_id, timeout=total_timeout-2):
        print(f"   Mode → {mode_name} (via COMMAND_LONG)")
    else:
        print(f"   ⚠ Timeout: modalità {mode_name} non raggiunta")


# ----------------------------------------------------------------------
# Sequenza operativa
# ----------------------------------------------------------------------
def run_sequence(master):
    print("\n1) SYSID_THISMAV → 21")
    set_param(master, "SYSID_THISMAV", 21)
    wait_for_sysid(master, 21)

    print("\n2) Modalità LOITER")
    set_mode(master, "LOITER")

    print("\n3) Pausa 5 s…")
    time.sleep(5)

    print("\n4) Modalità HOLD")
    set_mode(master, "HOLD")

    print("\n5) SYSID_THISMAV → 1")
    set_param(master, "SYSID_THISMAV", 1)
    wait_for_sysid(master, 1)

    print("\nSequenza completata.")

# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Switch SYSID + modalità")
    net = ap.add_mutually_exclusive_group(required=True)
    net.add_argument("--listen", type=int, metavar="PORT",
                     help="udpin:0.0.0.0:PORT (ascolto)")
    net.add_argument("--host", type=str, metavar="IP",
                     help="udpout verso IP veicolo")
    ap.add_argument("--port", type=int, default=14550,
                    help="porta remota (default 14550)")
    args = ap.parse_args()

    endpoint = f"udpin:0.0.0.0:{args.listen}" if args.listen else f"udpout:{args.host}:{args.port}"
    is_udpout = bool(args.host)

    print(f"Connessione a {endpoint} …")
    m = connect(endpoint, is_udpout)

    try:
        run_sequence(m)
    except Exception as e:
        print("Errore:", e)
    finally:
        m.close()
        print("Connessione chiusa.")


if __name__ == "__main__":
    main()
