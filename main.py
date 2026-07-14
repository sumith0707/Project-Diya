import os
import time
import sys

BOARD_IP = "192.168.0.102"
os.environ["ARDUINO_BOARD_IP"] = BOARD_IP

from arduino.app_utils import App, Bridge

def main():
    print(f"Connecting to {BOARD_IP}...")
    time.sleep(3)

    try:
        response = Bridge.call("ping")
        if response == 1:
            print("Ping OK. Monitoring obstacles (within 100cm)...")
        else:
            print("Ping failed.")
            return
    except Exception as e:
        print(f"Ping error: {e}")
        return

    # Track previous states
    prev_left = -1   # Use -1 to force first print
    prev_center = -1
    prev_right = -1

    print("-" * 40)

    try:
        while True:
            # Get obstacle status (should return 1 or 0)
            left = Bridge.call("read_left")
            center = Bridge.call("read_center")
            right = Bridge.call("read_right")

            # Ensure we have integers (convert None/False to 0)
            left = 1 if left == 1 else 0
            center = 1 if center == 1 else 0
            right = 1 if right == 1 else 0

            # Only print if any sensor state has changed
            if left != prev_left or center != prev_center or right != prev_right:
                # Build status string
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

            # Small delay to prevent CPU overload and reduce flicker
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
