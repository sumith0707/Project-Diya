import time
import sys
from arduino import rpc # Imports the native Uno Q communication library

def main():
    # REPLACE THIS with your Uno Q's actual local Wi-Fi IP address 
    BOARD_IP = "192.168.0.102" 
    
    print(f"Connecting to Diya's real-time core at {BOARD_IP}...")
    
    try:
        # Establish the network RPC connection to the board
        client = rpc.Client(BOARD_IP)
    except Exception as e:
        print(f"Error: Unable to reach the Arduino Uno Q. Details: {e}")
        print("Check if the board is powered and your PC is on the same network.")
        sys.exit(1)
        
    print("\n===========================================")
    print("   DIYA ULTRASONIC SENSOR ARRAY STREAM   ")
    print("===========================================")
    print("Press Ctrl+C to terminate data stream.\n")
    
    try:
        while True:
            # Query the specific memory slots updated by the C++ core
            left = client.get("left_sensor")
            center = client.get("center_sensor")
            right = client.get("right_sensor")
            
            # Formats values dynamically on a single terminal line
            # Values will display as '---' if they return a -1 error flag
            left_str = f"{left:6.1f} cm" if left and left > 0 else "   ---   "
            center_str = f"{center:6.1f} cm" if center and center > 0 else "   ---   "
            right_str = f"{right:6.1f} cm" if right and right > 0 else "   ---   "
            
            sys.stdout.write(f"\r[Left]: {left_str}  |  [Center]: {center_str}  |  [Right]: {right_str}")
            sys.stdout.flush()
            
            # Read rate matching the shared memory refresh cycle
            time.sleep(0.08) 
            
    except KeyboardInterrupt:
        print("\n\nStream safely closed by user.")

if __name__ == "__main__":
    main()

