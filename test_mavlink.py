#!/usr/bin/env python3
"""
Script di test per diagnosticare la connessione MAVLink
"""

import socket
import time
import struct
from pymavlink import mavutil

def test_raw_udp(ip, port):
    """Test connessione UDP raw per vedere cosa risponde il dispositivo"""
    print(f"Testing raw UDP connection to {ip}:{port}")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2)
    
    # Invia un heartbeat MAVLink semplice
    heartbeat = struct.pack('<BBBBBBBBB', 0xFE, 9, 0, 0, 6, 8, 0, 0, 3)
    
    try:
        sock.sendto(heartbeat, (ip, port))
        print("Heartbeat sent, waiting for response...")
        
        # Attendi risposta
        data, addr = sock.recvfrom(1024)
        print(f"Received {len(data)} bytes from {addr}:")
        print(f"Raw data: {data.hex()}")
        
        # Verifica se inizia con il magic byte MAVLink (0xFE)
        if len(data) > 0 and data[0] == 0xFE:
            print("✓ Data starts with MAVLink magic byte (0xFE)")
            print(f"Message length: {data[1] if len(data) > 1 else 'unknown'}")
        else:
            print("✗ Data does not start with MAVLink magic byte")
            
    except socket.timeout:
        print("No response received (timeout)")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        sock.close()

def test_mavutil_connection(ip, port):
    """Test connessione usando mavutil"""
    print(f"\nTesting mavutil connection to {ip}:{port}")
    
    try:
        mav = mavutil.mavlink_connection(
            f"udpout:{ip}:{port}",
            dialect="ardupilotmega",
            source_system=255,
            autoreconnect=True,
            timeout=2,
        )
        
        print("Connection created, sending heartbeat...")
        
        # Invia heartbeat
        for i in range(3):
            mav.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_GCS,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0, 0, 0
            )
            time.sleep(0.2)
        
        print("Heartbeats sent, waiting for response...")
        
        # Attendi heartbeat di risposta
        start_time = time.time()
        while time.time() - start_time < 5:
            msg = mav.recv_match(blocking=True, timeout=1)
            if msg:
                print(f"Received message: {msg}")
                if msg.get_type() == "HEARTBEAT":
                    print("✓ Heartbeat received!")
                    break
            else:
                print("No message received...")
        
        mav.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # Test entrambi i dispositivi
    devices = [
        ("10.8.0.54", 14550),
        ("10.8.0.54", 14551),
        ("10.8.0.55", 14550),
        ("10.8.0.55", 14551),
    ]
    
    for ip, port in devices:
        print(f"\n{'='*50}")
        print(f"Testing {ip}:{port}")
        print(f"{'='*50}")
        
        test_raw_udp(ip, port)
        test_mavutil_connection(ip, port) 