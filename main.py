import os
import time
import sys
import threading

# ========== Configuration ==========
BOARD_IP = "192.168.0.102"   # CHANGE to your board's actual IP
os.environ["ARDUINO_BOARD_IP"] = BOARD_IP

from arduino.app_utils import Bridge, App
from arduino.app_bricks.web_ui import WebUI

# ========== WebUI instance ==========
ui = WebUI()

# ========== WebSocket handler for raw text ==========
def on_raw_text(sid, message):
    """Called when a client sends a message with event 'raw_text'."""
    print(f"\n[Raw Text] Client {sid} sent: {message}")
    # Optional: send a reply back to the client
    ui.send_message("reply", f"UNO Q received: {message}", sid)

# Register the handler
ui.on_message("raw_text", on_raw_text)

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

    prev_left = -1
    prev_center = -1
    prev_right = -1

    print("-" * 40)
    print("Obstacle monitor running.")
    print("WebUI available at http://<BOARD_IP>:7000")
    print("Send WebSocket messages with event 'raw_text'")
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

            time.sleep(update_interval)

    except KeyboardInterrupt:
        print("\nObstacle monitor stopped.")

# ========== Main entry point ==========
def main():
    print("=" * 50)
    print("Diya 2 – Obstacle Detection + Raw Text WebSocket")
    print("=" * 50)

    # Start obstacle monitoring in a background thread
    obstacle_thread = threading.Thread(target=monitor_obstacles, daemon=True)
    obstacle_thread.start()
    time.sleep(2)  # give it a moment to initialise

    # App.run() starts the WebUI server and keeps the app alive
    App.run()

if __name__ == "__main__":
    main()
