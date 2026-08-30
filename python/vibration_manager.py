import time
import threading
from arduino.app_utils import Bridge


class VibrationManager:
    """
    Drives 3 vibration motors (Left / Center / Right) via Bridge.notify("vibrate", "L,C,R").

    Obstacle-avoidance behavior (always active):
      - 0 or 1 sensors blocked -> no vibration
      - exactly 2 blocked      -> pulse the single clear-direction motor: 2s on / 5s off, repeating
      - all 3 blocked          -> continuous vibration on all 3 motors
      A change in obstacle pattern immediately resets/restarts the cycle.

    Navigation turn-cue behavior (overlaid on top, only while active):
      - double-pulse on the turn-direction motor, repeating every TURN_CYCLE_SEC,
        until on_turn_end() is called (turn confirmed / timed out / rerouted)
      - takes priority over the obstacle pattern on that same motor while active
    """

    PULSE_ON_SEC = 2.0
    PULSE_CYCLE_SEC = 7.0  # 2s on + 5s off

    TURN_PULSE_ON_SEC = 0.3
    TURN_PULSE_GAP_SEC = 0.3
    TURN_CYCLE_SEC = 4.0  # two short pulses, then rest, repeating

    SEND_INTERVAL_SEC = 0.1

    def __init__(self, get_obstacle_state):
        """
        get_obstacle_state: callable returning (left_blocked, center_blocked, right_blocked) as bools
        """
        self.get_obstacle_state = get_obstacle_state

        self._lock = threading.Lock()
        self._running = False
        self._thread = None

        # obstacle pattern state
        self._last_blocked_state = None
        self._current_pattern = None   # None | 'pulse' | 'all'
        self._pulse_motor = None       # 'L' | 'C' | 'R'
        self._pattern_start_time = 0.0

        # turn cue state
        self._turn_active = False
        self._turn_motor = None        # 'L' | 'R'
        self._turn_cycle_start = 0.0

        self._last_sent = (0, 0, 0)

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[Vibration] Manager started.")

    def stop(self):
        self._running = False
        self._send(0, 0, 0)
        print("[Vibration] Manager stopped.")

    # ------------------------------------------------------------
    # Navigation hooks (called from OsmNavigationEngine)
    # ------------------------------------------------------------
    def on_turn_start(self, direction):
        """direction: 'left' or 'right'"""
        motor = "L" if direction == "left" else ("R" if direction == "right" else None)
        if motor is None:
            return
        with self._lock:
            self._turn_active = True
            self._turn_motor = motor
            self._turn_cycle_start = 0.0  # forces immediate restart of the pulse cycle
        print(f"[Vibration] Turn cue started: {direction} ({motor}).")

    def on_turn_end(self):
        with self._lock:
            self._turn_active = False
            self._turn_motor = None
        print("[Vibration] Turn cue ended.")

    # ------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------
    def _loop(self):
        while self._running:
            now = time.time()

            left, center, right = self.get_obstacle_state()
            blocked = (bool(left), bool(center), bool(right))
            blocked_count = sum(blocked)

            if blocked_count <= 1:
                target_pattern = None
                target_motor = None
            elif blocked_count == 2:
                target_pattern = "pulse"
                if not blocked[0]:
                    target_motor = "L"
                elif not blocked[1]:
                    target_motor = "C"
                else:
                    target_motor = "R"
            else:
                target_pattern = "all"
                target_motor = None

            if blocked != self._last_blocked_state:
                self._last_blocked_state = blocked
                self._current_pattern = target_pattern
                self._pulse_motor = target_motor
                self._pattern_start_time = now

            out_l, out_c, out_r = 0, 0, 0

            if self._current_pattern == "all":
                out_l, out_c, out_r = 1, 1, 1
            elif self._current_pattern == "pulse" and self._pulse_motor:
                elapsed = (now - self._pattern_start_time) % self.PULSE_CYCLE_SEC
                on = 1 if elapsed < self.PULSE_ON_SEC else 0
                if self._pulse_motor == "L":
                    out_l = on
                elif self._pulse_motor == "C":
                    out_c = on
                else:
                    out_r = on

            # Overlay turn cue - takes priority on its motor
            with self._lock:
                turn_active = self._turn_active
                turn_motor = self._turn_motor
                if turn_active and self._turn_cycle_start == 0.0:
                    self._turn_cycle_start = now
                turn_cycle_start = self._turn_cycle_start

            if turn_active and turn_motor:
                elapsed = (now - turn_cycle_start) % self.TURN_CYCLE_SEC
                p1_end = self.TURN_PULSE_ON_SEC
                p2_start = p1_end + self.TURN_PULSE_GAP_SEC
                p2_end = p2_start + self.TURN_PULSE_ON_SEC
                turn_on = 1 if (elapsed < p1_end or p2_start <= elapsed < p2_end) else 0

                if turn_motor == "L":
                    out_l = turn_on
                else:
                    out_r = turn_on

            self._send(out_l, out_c, out_r)
            time.sleep(self.SEND_INTERVAL_SEC)

    def _send(self, l, c, r):
        state = (l, c, r)
        if state == self._last_sent:
            return
        self._last_sent = state
        try:
            Bridge.notify("vibrate", f"{l},{c},{r}")
        except Exception as e:
            print(f"[Vibration] Error sending command: {e}")