import os
import time
import sys

# Configuration
BOARD_IP = "192.168.0.102"
os.environ["ARDUINO_BOARD_IP"] = BOARD_IP

from arduino.app_utils import Bridge

def monitor_obstacles(threshold_cm=100, update_interval=0.05):
    """
    Continuously monitor ultrasonic sensors and print status when obstacles
    are detected within the given threshold.

    Args:
        threshold_cm (int): Distance in cm to consider as obstacle.
        update_interval (float): Sleep time between sensor reads (seconds).

    Returns:
        None (runs indefinitely until KeyboardInterrupt)
    """
    print(f"Connecting to {BOARD_IP}...")
    time.sleep(3)

    # Ping the board
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

    # Track previous states to detect changes
    prev_left = -1
    prev_center = -1
    prev_right = -1

    print("-" * 40)

    try:
        while True:
            # Read raw obstacle status (1 = obstacle, 0 = clear)
            left = Bridge.call("read_left")
            center = Bridge.call("read_center")
            right = Bridge.call("read_right")

            # Normalize to 0/1 (safety against None or other values)
            left = 1 if left == 1 else 0
            center = 1 if center == 1 else 0
            right = 1 if right == 1 else 0

            # Only print if any sensor state changed
            if left != prev_left or center != prev_center or right != prev_right:
                status = ""
                status += "L" if left else "-"
                status += "C" if center else "-"
                status += "R" if right else "-"

                timestamp = time.strftime("%H:%M:%S")
                print(f"[{timestamp}] Status: [{status}]")

                # Update previous states
                prev_left = left
                prev_center = center
                prev_right = right

            time.sleep(update_interval)

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")

def main():
    """Entry point for the script."""
    monitor_obstacles()

if __name__ == "__main__":
    main()