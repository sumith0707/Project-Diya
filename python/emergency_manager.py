import threading
import time
import math
from arduino.app_utils import Bridge

class EmergencyManager:
    def __init__(self, imu=None):
        """
        Initialize the Emergency Manager.
        
        :param imu: IMUReader instance for fall detection (optional).
        """
        self.ui = None
        self.timer = None
        self.active = False
        self.lock = threading.Lock()
        self.imu = imu
        
        # Fall detection state
        self.fall_detected = False
        self.last_fall_time = 0
        self.fall_cooldown = 10  # Seconds to wait before detecting another fall
        
        # Fall detection parameters (tune these)
        self.FALL_ACCEL_THRESHOLD = 25.0   # m/s² - high acceleration spike
        self.FALL_POST_THRESHOLD = 5.0     # seconds of inactivity after fall
        self.FALL_INACTIVITY_THRESHOLD = 2.0  # m/s² - below this = inactive

    def set_ui(self, ui):
        """Set the WebUI instance for sending messages."""
        self.ui = ui

    def set_imu(self, imu):
        """Set the IMU reader for fall detection."""
        self.imu = imu

    def trigger_emergency(self, source="button"):
        """
        Start the 5‑second emergency countdown.
        
        :param source: "button" or "fall" – determines the message sent.
        """
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None

            self.active = True
            self._emergency_source = source
            self.timer = threading.Timer(5.0, self._send_emergency)
            self.timer.start()
            print(f"[Emergency] Timer started ({source}) – waiting 5 seconds for cancellation.")

    def cancel_emergency(self):
        """Cancel the countdown if it's active."""
        with self.lock:
            if self.active:
                if self.timer:
                    self.timer.cancel()
                    self.timer = None
                self.active = False
                print("[Emergency] Cancelled – no alert sent.")
                if self.ui:
                    self.ui.send_message("sos", "SOS_CANCELLED")
            else:
                print("[Emergency] Cancel ignored – no active emergency.")

    def _send_emergency(self):
        """Called when the timer expires."""
        with self.lock:
            self.active = False
            self.timer = None
            source = getattr(self, '_emergency_source', 'button')
        
        print(f"[Emergency] Timer expired – sending alert! (source: {source})")
        
        if self.ui:
            if source == "fall":
                self.ui.send_message("sos", "Fall detected")
            else:
                self.ui.send_message("sos", "SOS")

    # ========== Fall Detection ==========
    def start_fall_detection(self):
        """Start the fall detection loop in a background thread."""
        if self.imu is None:
            print("[Emergency] Fall detection: No IMU available.")
            return

        fall_thread = threading.Thread(target=self._fall_detection_loop, daemon=True)
        fall_thread.start()
        print("[Emergency] Fall detection started.")

    def _fall_detection_loop(self):
        """
        Continuously monitor IMU data for fall patterns.
        Runs in a separate background thread.
        """
        print("[Emergency] Fall detection loop running.")
        
        # State machine
        STATE_NORMAL = 0
        STATE_IMPACT = 1
        STATE_INACTIVITY = 2
        state = STATE_NORMAL
        
        impact_time = 0
        inactivity_start = 0
        
        while True:
            try:
                # Get accelerometer data
                accel = self._get_accelerometer()
                if accel is None:
                    time.sleep(0.1)
                    continue

                magnitude = self._compute_accel_magnitude(accel)
                #print(f"[Fall Debug] Accel: {magnitude:.2f} m/s²")  # Uncomment for debugging

                if state == STATE_NORMAL:
                    # Look for a sudden high acceleration spike
                    if magnitude > self.FALL_ACCEL_THRESHOLD:
                        print(f"[Fall] Impact detected! Magnitude: {magnitude:.2f} m/s²")
                        state = STATE_IMPACT
                        impact_time = time.time()
                        inactivity_start = time.time()

                elif state == STATE_IMPACT:
                    # After impact, check for period of inactivity (low movement)
                    if magnitude < self.FALL_INACTIVITY_THRESHOLD:
                        inactivity_duration = time.time() - inactivity_start
                        if inactivity_duration > self.FALL_POST_THRESHOLD:
                            # Fall confirmed!
                            print(f"[Fall] Fall detected! Inactive for {inactivity_duration:.1f}s")
                            self._on_fall_detected()
                            state = STATE_NORMAL
                            # Cooldown to prevent multiple triggers
                            time.sleep(self.fall_cooldown)
                        else:
                            # Still waiting for inactivity
                            time.sleep(0.1)
                    else:
                        # Movement detected – reset inactivity timer
                        inactivity_start = time.time()
                        # If too much time passes, reset to normal
                        if time.time() - impact_time > 5.0:
                            state = STATE_NORMAL

                time.sleep(0.05)  # 20Hz

            except Exception as e:
                print(f"[Fall] Error: {e}")
                time.sleep(0.5)

    def _get_accelerometer(self):
        """
        Get accelerometer data from the IMU.
        Returns: (x, y, z) in m/s² or None if error.
        """
        if self.imu is None:
            return None
        
        try:
            # Use the same IMU reader to get accelerometer data.
            # We need to extend the IMUReader to provide this.
            # For now, we'll use a placeholder – you'll need to extend IMUReader.
            # Option A: Extend IMUReader to have get_accelerometer()
            # Option B: Read directly from Bridge.call()
            
            # If IMUReader has get_accelerometer method:
            if hasattr(self.imu, 'get_accelerometer'):
                return self.imu.get_accelerometer()
            else:
                # Fallback: read from MCU if you add a Bridge.provide("get_accel")
                # For now, return dummy data (0, 0, 9.8) to simulate idle
                # This is a placeholder – you must implement this properly.
                return (0.0, 0.0, 9.8)
                
        except Exception as e:
            print(f"[Fall] IMU read error: {e}")
            return None

    def _compute_accel_magnitude(self, accel):
        """Compute the magnitude of the acceleration vector."""
        x, y, z = accel
        return math.sqrt(x*x + y*y + z*z)

    def _on_fall_detected(self):
        """Called when a fall is confirmed."""
        # Check cooldown
        current_time = time.time()
        if current_time - self.last_fall_time < self.fall_cooldown:
            print("[Fall] Cooldown active – ignoring.")
            return
        
        self.last_fall_time = current_time
        print("[Fall] Fall confirmed! Triggering emergency.")
        
        # Trigger the emergency timer with "fall" as the source
        self.trigger_emergency(source="fall")