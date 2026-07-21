import os
import time
import sys
import threading
import json

# ========== Configuration ==========
BOARD_IP = "192.168.0.105"   # CHANGE to your board's actual IP
os.environ["ARDUINO_BOARD_IP"] = BOARD_IP

from arduino.app_utils import Bridge, App
from arduino.app_bricks.web_ui import WebUI
from osm_nav import OsmNavigationEngine
from emergency_manager import EmergencyManager
from voice_recognition import VoiceRecognition

sos_timer = None          # Timer object for delayed SOS
sos_active = False        # True while waiting for SOS confirmation

# ========== WebUI instance ==========
ui = WebUI()
emergency = EmergencyManager(ui)

# Hardcoded destination name string (e.g., "Majestic, Bengaluru")
DESTINATION_NAME = "Mangalore" 
nav_engine = OsmNavigationEngine(destination_name=DESTINATION_NAME)

# ========== Shared GPS data (for future use) ==========
gps_data = {
    "lat": 0.0,
    "lng": 0.0,
    "fix": False,
    "last_update": 0.0
}
gps_lock = threading.Lock()

# ========== Voice Recognition Setup ==========

try:
    voice = VoiceRecognition(
        model_name="tiny",      # Use tiny model
        device="cpu",
        compute_type="int8",    # Optimized for CPU
        block_duration_ms=30,
        silence_timeout=1.5,
        vad_mode=1
    )
    print("Voice recognition engine ready (Faster Whisper tiny).")
except Exception as e:
    print(f"Failed to initialize Faster Whisper: {e}")
    voice = None
    
# # ========== WebSocket handler for raw text ==========
def on_raw_text(sid, message):
    print(f"\n[Raw Text] Client {sid} sent: {message}")
    ui.send_message("reply", f"UNO Q received: {message}", sid)
    if message == "start":
        print("Nav starting")
        nav_engine.update_live_gps(lat2, lng2)
        print("Nav started")

# ========== WebSocket handler for GPS data ==========
def on_gps(sid, message):
    """
    Expects a JSON string: {"lat": 12.345, "lng": 67.890}
    """
    global lat2, lng2
    try:
        # If message is a string, parse it as JSON
        if isinstance(message, str):
            data = json.loads(message)
        else:
            data = message

        lat = float(data.get("lat", 0))
        lng = float(data.get("lng", 0))

        # Validate coordinates (optional)
        # if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        #     print(f"\n[GPS] Invalid coordinates from {sid}: lat={lat}, lng={lng}")
        #     return

        with gps_lock:
            gps_data["lat"] = lat
            gps_data["lng"] = lng
            gps_data["fix"] = True
            gps_data["last_update"] = time.time()

        

        print(f"\n[GPS] {sid} -> Lat: {lat:.6f}, Lng: {lng:.6f}")
        lat2=lat
        lng2=lng
        # Optional: send acknowledgment
        # ui.send_message("gps_ack", {"status": "ok", "lat": lat, "lng": lng}, sid)

    except json.JSONDecodeError:
        print(f"\n[GPS] Invalid JSON from {sid}: {message}")
    except Exception as e:
        print(f"\n[GPS] Error: {e}")

# ========== Register handlers ==========
ui.on_message("raw_text", on_raw_text)
ui.on_message("gps", on_gps)

obstacle_data = {"left": 0, "center": 0, "right": 0}
obstacle_lock = threading.Lock()

# ========== Handler for ultrasonic notifications ==========
def on_ultrasonic(data):
    """Called when MCU pushes ultrasonic data via Bridge.notify()"""
    try:
        # Data is a comma-separated string: "1,0,1"
        parts = data.split(",")
        if len(parts) == 3:
            left = int(parts[0])
            center = int(parts[1])
            right = int(parts[2])
            with obstacle_lock:
                obstacle_data["left"] = left
                obstacle_data["center"] = center
                obstacle_data["right"] = right
            # Optional: print only on change
            # print(f"Ultrasonic: L={left}, C={center}, R={right}")
    except Exception as e:
        print(f"Ultrasonic parse error: {e}")

Bridge.provide("ultrasonic", on_ultrasonic)

# ========== Obstacle monitoring (runs in a background thread) ==========
def monitor_obstacles(update_interval=0.05):
    print("Obstacle monitor running (using Bridge.notify()).")
    prev_state = (-1, -1, -1)

    try:
        while True:
            with obstacle_lock:
                left = obstacle_data["left"]
                center = obstacle_data["center"]
                right = obstacle_data["right"]

            if (left, center, right) != prev_state:
                status = ""
                status += "L" if left else "-"
                status += "C" if center else "-"
                status += "R" if right else "-"
                timestamp = time.strftime("%H:%M:%S")
                print(f"[{timestamp}] Obstacles: [{status}]")
                prev_state = (left, center, right)

            time.sleep(update_interval)
    except KeyboardInterrupt:
        print("Obstacle monitor stopped.")

# def send_sos():
#     """Called after 5 seconds if SOS is not cancelled."""
#     global sos_active, sos_timer
#     print("[SOS] Timer expired – sending SOS alert!")
#     ui.send_message("sos", "SOS")   # Broadcast to all clients
#     sos_active = False
#     sos_timer = None

# # ========== SOS Handler ==========
# def on_sos(state):
#     global sos_active, sos_timer
#     print(f"[SOS] Received state: {state}")

#     if state == "sos":
#         # Short press – start timer
#         if sos_active:
#             # Cancel any existing timer (shouldn't happen normally)
#             if sos_timer:
#                 sos_timer.cancel()
#                 sos_timer = None
#         # Start new 5-second timer
#         sos_active = True
#         sos_timer = threading.Timer(5.0, send_sos)
#         sos_timer.start()
#         print("[SOS] Timer started – waiting 5 seconds for cancellation.")

#     elif state == "sos_cancel":
#         # Long press – cancel if timer is active
#         if sos_active:
#             if sos_timer:
#                 sos_timer.cancel()
#                 sos_timer = None
#             sos_active = False
#             print("[SOS] Cancelled – no alert sent.")
#             ui.send_message("sos", "SOS_CANCELLED")   # Optional: inform UI
#         else:
#             # Long press without active timer – ignore
#             print("[SOS] Long press ignored (no active SOS).")

def on_sos(state):
    #print(f"[SOS] SOS state: {state}")
    if state == "sos":
        emergency.trigger_emergency()   # ← Start timer
    elif state == "sos_cancel":
        emergency.cancel_emergency()     # ← Cancel timer

def handle_voice_result(text):
    """Called when Vosk successfully recognizes speech."""
    print(f"[Voice Result] {text}")
    # Send to WebUI
    ui.send_message("voice_command", text)
    # You can add command parsing here, e.g.:
    # if "navigate" in text.lower():
    #     # extract destination and start navigation
    #     pass

def on_mul(state):
    print(f"[Mul_Pur] Button pressed: {state}")
    if voice is None:
        print("Voice recognition not available.")
        return
    if state == "short":
        print("Starting voice recognition...")
        voice.start_recording(callback=handle_voice_result)
    else:  # long press
        print("Cancelling voice recognition...")
        voice.stop_recording()
    
Bridge.provide("SOS", on_sos)
Bridge.provide("Mul_Pur", on_mul)

# ========== Main entry point ==========
def main():
    print("=" * 50)
    print("Diya 2 – Obstacle Detection + Raw Text + GPS")
    print("=" * 50)

    obstacle_thread = threading.Thread(target=monitor_obstacles, daemon=True)
    obstacle_thread.start()
    time.sleep(2)

    App.run()

if __name__ == "__main__":
    main()