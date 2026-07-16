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

# ========== WebUI instance ==========
ui = WebUI()

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

# ========== Obstacle monitoring (runs in a background thread) ==========
def monitor_obstacles(threshold_cm=100, update_interval=0.05):
    print(f"Connecting to {BOARD_IP}...")
    time.sleep(3)

    try:
        response = Bridge.call("ping")
        if response == 1:
            print(f"Ping OK. Monitoring obstacles within {threshold_cm}cm...")
        else:
            print("Ping failed.")
            return
    except Exception as e:
        print(f"Ping error: {e}")
        return

    prev_left = prev_center = prev_right = -1

    print("-" * 40)
    print("Obstacle monitor running.")
    print("WebUI available at http://<BOARD_IP>:7000")
    print("Send 'raw_text' for plain text, or 'gps' with JSON: {\"lat\": X, \"lng\": Y}")
    print("-" * 40)

    try:
        while True:
            left = Bridge.call("read_left")
            center = Bridge.call("read_center")
            right = Bridge.call("read_right")

            left = 1 if left == 1 else 0
            center = 1 if center == 1 else 0
            right = 1 if right == 1 else 0

            if left != prev_left or center != prev_center or right != prev_right:
                status = ""
                status += "L" if left else "-"
                status += "C" if center else "-"
                status += "R" if right else "-"
                timestamp = time.strftime("%H:%M:%S")
                print(f"[{timestamp}] Obstacles: [{status}]")
                prev_left, prev_center, prev_right = left, center, right

            # Optional: print current GPS every 5 seconds (for debugging)
            # with gps_lock:
            #     if gps_data["fix"] and (time.time() - gps_data["last_update"] < 10):
            #         print(f"\r[GPS] {gps_data['lat']:.6f}, {gps_data['lng']:.6f}", end="")

            time.sleep(update_interval)

    except KeyboardInterrupt:
        print("\nObstacle monitor stopped.")

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