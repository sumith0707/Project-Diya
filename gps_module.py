import os
import time
import sys

# ============================================================
# CONFIGURATION
# ============================================================
BOARD_IP = "192.168.0.102"
os.environ["ARDUINO_BOARD_IP"] = BOARD_IP
from arduino.app_utils import Bridge

OBSTACLE_THRESHOLD_CM = 100   # for info only (Arduino uses its own)
UPDATE_INTERVAL = 0.05        # 20 Hz


# ============================================================
# MAIN FUNCTION – monitors both obstacles and GPS
# ============================================================

def monitor_all(threshold_cm=100, update_interval=0.05):
    """
    Continuously reads obstacle status and GPS data from the Arduino.
    Prints any changes with timestamps.
    """
    print(f"Connecting to {BOARD_IP}...")
    time.sleep(3)

    # Ping the board
    try:
        if Bridge.call("ping") != 1:
            print("Ping failed.")
            return
    except Exception as e:
        print(f"Ping error: {e}")
        return

    print(f"Ping OK. Monitoring obstacles (within {threshold_cm}cm) and GPS.")
    print("-" * 50)

    # ---------- Track previous states ----------
    # Obstacles
    prev_left = prev_center = prev_right = -1
    # GPS
    prev_lat = prev_lng = None
    prev_fix = -1

    try:
        while True:
            changed = False
            output_lines = []

            # ---------- 1. Read Obstacles ----------
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
                output_lines.append(f"Obstacles: [{status}]")
                prev_left, prev_center, prev_right = left, center, right
                changed = True

            # ---------- 2. Read GPS ----------
            lat = Bridge.call("get_lat")
            lng = Bridge.call("get_lng")
            fix = 1 if Bridge.call("get_gps_fix") == 1 else 0

            # Convert None to 0.0
            lat = float(lat) if lat is not None else 0.0
            lng = float(lng) if lng is not None else 0.0
            #print(lat,lng)
            # GPS fix status change
            if fix != prev_fix:
                output_lines.append(f"GPS Fix: {'YES' if fix else 'NO'}")
                prev_fix = fix
                changed = True

            # GPS coordinate change (only if we have a fix)
            if fix and (lat != prev_lat or lng != prev_lng):
                output_lines.append(f"Location: {lat:.6f}, {lng:.6f}")
                prev_lat, prev_lng = lat, lng
                changed = True

            # ---------- Print if anything changed ----------
            if changed:
                timestamp = time.strftime("%H:%M:%S")
                print(f"\n[{timestamp}]")
                for line in output_lines:
                    print(f"  {line}")

            time.sleep(update_interval)

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    monitor_all()

if __name__ == "__main__":
    main()