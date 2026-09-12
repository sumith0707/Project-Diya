import math
import time
import requests
import threading
from arduino.app_utils import brick
from imu_module import IMUReader

class GeoMath:
    @staticmethod
    def distance_in_meters(lat1, lon1, lat2, lon2):
        """Calculates distance between two points using the Haversine formula."""
        R = 6371000  # Radius of the Earth in meters
        p_lat1, p_lat2 = math.radians(lat1), math.radians(lat2)
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        
        a = math.sin(d_lat/2)**2 + math.cos(p_lat1) * math.cos(p_lat2) * math.sin(d_lon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

@brick
class OsmNavigationEngine:
    def __init__(self, destination_name):
        self.destination_name = destination_name
        self.dest_lat = None
        self.dest_lon = None
        
        self.current_lat = None
        self.current_lon = None
        
        self.steps_queue = []
        self.active_step = None
        
        self.is_navigating = False
        self.route_initialized = False
        self.trigger_radius = 15
        self.reroute_threshold = 100

        # Thread safety
        self._lock = threading.RLock()

        # Error recovery
        self._retry_attempts = 3
        self._retry_delay = 2

        # Direction detection
        self.last_distance_to_turn = None

        # Debounce
        self.off_route_count = 0
        self.off_route_threshold = 3

        # IMU turn confirmation
        self.imu = None
        self.waiting_for_turn = False
        self.turn_start_heading = None
        self.turn_start_time = None
        self.turn_timeout = 15
        self.turn_threshold = 30
        self.turn_direction = None

        # Turn vibration callbacks
        self.on_turn_start_cb = None
        self.on_turn_end_cb = None

    def set_imu(self, imu_reader):
        """Set the IMU reader instance."""
        self.imu = imu_reader
        print("[OSM Nav] IMU reader attached.")

    def on_turn_start(self, callback):
        """Register callback fired when a turn-confirmation wait begins. callback(direction: str)"""
        self.on_turn_start_cb = callback

    def on_turn_end(self, callback):
        """Register callback fired when a turn-confirmation wait ends (confirmed, timed out, or rerouted)."""
        self.on_turn_end_cb = callback

    def _begin_turn_wait(self, direction):
        self.waiting_for_turn = True
        self.turn_direction = direction
        if self.on_turn_start_cb:
            try:
                self.on_turn_start_cb(direction)
            except Exception as e:
                print(f"[OSM Nav] Turn-start callback error: {e}")

    def _end_turn_wait(self):
        self.waiting_for_turn = False
        if self.on_turn_end_cb:
            try:
                self.on_turn_end_cb()
            except Exception as e:
                print(f"[OSM Nav] Turn-end callback error: {e}")

    def _resolve_destination_coords(self):
        """Resolves the hardcoded text string into coordinate pairs using OSM Nominatim."""
        print(f"[OSM Nav] Resolving destination coordinates for: {self.destination_name}...")
        url = "https://nominatim.openstreetmap.org/search"
        headers = {"User-Agent": "Diya_Navigation_System_v2"}
        params = {"q": self.destination_name, "format": "json", "limit": 1}
        
        for attempt in range(self._retry_attempts):
            try:
                response = requests.get(url, params=params, headers=headers, timeout=5)
                if response.status_code != 200:
                    print(f"[OSM Nav] Nominatim error: HTTP {response.status_code}")
                    time.sleep(self._retry_delay)
                    continue
                data = response.json()
                if data:
                    self.dest_lat = float(data[0]["lat"])
                    self.dest_lon = float(data[0]["lon"])
                    print(f"[OSM Nav] Resolved Target -> Lat: {self.dest_lat}, Lng: {self.dest_lon}")
                    return True
                else:
                    print("[OSM Nav] No results found for destination.")
                    return False
            except Exception as e:
                print(f"[OSM Nav] Geocoding error (attempt {attempt+1}): {e}")
                time.sleep(self._retry_delay)
        print("[OSM Nav] Geocoding failed after retries.")
        return False

    def initialize_route(self):
        """Fetches the primary step queue array from the OSRM server."""
        with self._lock:
            if not self.dest_lat or not self.current_lat:
                return False
            current_lon = self.current_lon
            current_lat = self.current_lat
            dest_lon = self.dest_lon
            dest_lat = self.dest_lat

        url = f"http://router.project-osrm.org/route/v1/walking/{current_lon},{current_lat};{dest_lon},{dest_lat}"
        params = {"steps": "true", "overview": "false"}

        for attempt in range(self._retry_attempts):
            try:
                response = requests.get(url, params=params, timeout=5)
                if response.status_code != 200:
                    print(f"[OSM Nav] OSRM error: HTTP {response.status_code}")
                    time.sleep(self._retry_delay)
                    continue
                data = response.json()
                if data.get("code") == "Ok":
                    steps = data["routes"][0]["legs"][0]["steps"]
                    with self._lock:
                        self.steps_queue = steps
                        if self.steps_queue:
                            self.active_step = self.steps_queue.pop(0)
                            self.is_navigating = True
                            self.route_initialized = True
                            self.last_distance_to_turn = None
                            self.off_route_count = 0
                            self.announce_current_step()
                            return True
                        else:
                            print("[OSM Nav] Route returned zero steps.")
                            return False
                else:
                    print(f"[OSM Nav] OSRM error: {data.get('code')}")
                    time.sleep(self._retry_delay)
            except Exception as e:
                print(f"[OSM Nav] Route download error (attempt {attempt+1}): {e}")
                time.sleep(self._retry_delay)
        print("[OSM Nav] Route fetch failed after retries.")
        return False

    def update_live_gps(self, lat, lon):
        """Receives and caches telemetry coordinates updated from your main script channel."""
        with self._lock:
            self.current_lat = lat
            self.current_lon = lon
        
        if not self.route_initialized and lat != 0.0 and lon != 0.0:
            if self._resolve_destination_coords():
                self.initialize_route()

    def announce_current_step(self):
        """Prints the natural language instructions out directly on the MPU log lines."""
        with self._lock:
            if not self.active_step:
                return
            step = self.active_step
    
        maneuver = step["maneuver"]
        m_type = maneuver.get("type", "")
        modifier = maneuver.get("modifier", "")
        street = step.get("name", "").strip()
    
        modifier_map = {
            "right": "to the right",
            "left": "to the left",
            "straight": "straight",
            "slight right": "slightly to the right",
            "slight left": "slightly to the left",
            "sharp right": "sharply to the right",
            "sharp left": "sharply to the left",
            "uturn": "around"
        }
    
        if m_type == "depart":
            if street:
                text = f"Proceed onto {street}."
            else:
                mod_text = modifier_map.get(modifier, modifier)
                text = f"Proceed {mod_text}."
        elif "turn" in m_type:
            if street:
                text = f"Turn {modifier} onto {street}."
            else:
                text = f"Turn {modifier}."
        elif m_type == "arrive":
            text = f"You have arrived at your destination."
        else:
            if street:
                text = f"Continue along {street}."
            else:
                text = f"Continue straight."
    
        print(f"\n>>>> [DIYA NAVIGATION DIRECTIVE]: {text} <<<<\n")

    def reroute(self):
        """Force a reroute from the current position to the destination."""
        print("[OSM Nav] Rerouting...")
        self._end_turn_wait()
        with self._lock:
            self.is_navigating = False
            self.route_initialized = False
            self.steps_queue = []
            self.active_step = None
            self.last_distance_to_turn = None
            self.off_route_count = 0
        self.initialize_route()

    def _advance_to_next_step(self):
        """Advance to the next step in the queue (or finish navigation)."""
        with self._lock:
            if self.steps_queue:
                self.active_step = self.steps_queue.pop(0)
                self.last_distance_to_turn = None
                self.off_route_count = 0
                self.announce_current_step()
            else:
                print(f"\n>>>> [DIYA NAVIGATION DIRECTIVE]: You have arrived at your destination: {self.destination_name} <<<<\n")
                self.is_navigating = False
                self.active_step = None
                self.route_initialized = False
                self.waiting_for_turn = False

    @brick.loop
    def process_navigation(self):
        """Continuously computes proximity constraints on the active background thread."""
        with self._lock:
            if not self.is_navigating or not self.active_step or not self.current_lat:
                time.sleep(1)
                return
            active_step = self.active_step
            current_lat = self.current_lat
            current_lon = self.current_lon
            steps_queue_exists = bool(self.steps_queue)

        target_lon, target_lat = active_step["maneuver"]["location"]
        distance_to_turn = GeoMath.distance_in_meters(
            current_lat, current_lon, target_lat, target_lon
        )

        # ========== ROUTE REFRESH WITH DIRECTION DETECTION + DEBOUNCE ==========
        if self.last_distance_to_turn is not None and self.route_initialized:
            if distance_to_turn > self.last_distance_to_turn:
                if distance_to_turn > self.reroute_threshold:
                    self.off_route_count += 1
                    print(f"[OSM Nav] Off-route detection {self.off_route_count}/{self.off_route_threshold} - Distance: {distance_to_turn:.1f}m")
                    if self.off_route_count >= self.off_route_threshold:
                        if steps_queue_exists or self.active_step:
                            print(f"[OSM Nav] Off-route confirmed! Rerouting...")
                            self.reroute()
                            self.last_distance_to_turn = None
                            self.off_route_count = 0
                            time.sleep(1)
                            return
                else:
                    self.off_route_count = 0
            else:
                if self.off_route_count > 0:
                    self.off_route_count = 0
                    print("[OSM Nav] Back on route - reset off-route counter")
        
        self.last_distance_to_turn = distance_to_turn

        # ========== TURN CONFIRMATION LOGIC ==========
        is_turn_step = False
        turn_modifier = None
        if self.active_step:
            maneuver = self.active_step.get("maneuver", {})
            m_type = maneuver.get("type", "")
            modifier = maneuver.get("modifier", "")
            if "turn" in m_type and modifier in ("left", "right"):
                is_turn_step = True
                turn_modifier = modifier

        if self.waiting_for_turn:
            if self.imu is None:
                print("[OSM Nav] IMU not available – skipping turn confirmation.")
                self._end_turn_wait()
                self._advance_to_next_step()
                time.sleep(1)
                return

            current_heading = self.imu.get_heading()
            heading_change = self.imu.get_relative_heading(self.turn_start_heading)

            print(
                f"[OSM Nav] [DEBUG] direction={self.turn_direction}, "
                f"start_heading={self.turn_start_heading:.1f}, "
                f"current_heading={current_heading:.1f}, "
                f"heading_change={heading_change:.1f}, "
                f"threshold={self.turn_threshold}"
            )

            expected_sign = -1 if self.turn_direction == "left" else 1
            if expected_sign * heading_change > self.turn_threshold:
                print(f"[OSM Nav] Turn confirmed! Heading changed by {heading_change:.1f}°.")
                self._end_turn_wait()
                self._advance_to_next_step()
                time.sleep(1)
                return

            if time.time() - self.turn_start_time > self.turn_timeout:
                print(f"[OSM Nav] Turn not detected within {self.turn_timeout}s – rerouting.")
                self.reroute()
                time.sleep(1)
                return

            time.sleep(0.5)
            return

        # ========== STEP ADVANCEMENT ==========
        if distance_to_turn < self.trigger_radius:
            if is_turn_step and self.imu is not None:
                if not self.waiting_for_turn:
                    print(f"[OSM Nav] Preparing for {turn_modifier} turn. Waiting for IMU confirmation...")
                    self.turn_start_heading = self.imu.get_heading()
                    self.turn_start_time = time.time()
                    self._begin_turn_wait(turn_modifier)
                    self.announce_current_step()
                    time.sleep(1)
                    return
                else:
                    time.sleep(0.5)
                    return
            else:
                self._advance_to_next_step()
                time.sleep(1)
                return