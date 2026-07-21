import threading
from arduino.app_utils import Bridge

class EmergencyManager:
    def __init__(self, ui):
        self.ui = ui
        self.timer = None
        self.active = False
        self.lock = threading.Lock()  # Thread‑safe

    def trigger_emergency(self):
        """Start the 5‑second emergency countdown."""
        with self.lock:
            # Cancel any existing timer
            if self.timer:
                self.timer.cancel()
                self.timer = None

            self.active = True
            self.timer = threading.Timer(5.0, self._send_emergency)
            self.timer.start()
            print("[Emergency] Timer started – waiting 5 seconds for cancellation.")

    def cancel_emergency(self):
        """Cancel the countdown if it's active."""
        with self.lock:
            if self.active:
                if self.timer:
                    self.timer.cancel()
                    self.timer = None
                self.active = False
                print("[Emergency] Cancelled – no alert sent.")
                self.ui.send_message("sos", "SOS_CANCELLED")
            else:
                print("[Emergency] Cancel ignored – no active emergency.")

    def _send_emergency(self):
        """Called when the timer expires."""
        with self.lock:
            self.active = False
            self.timer = None
        print("[Emergency] Timer expired – sending alert!")
        self.ui.send_message("sos", "SOS")