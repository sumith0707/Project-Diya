"""
Feeds simulated GPS coordinates into a running OsmNavigationEngine so
navigation can be demoed without physically walking an outdoor route.

Only the lat/lon feed is faked here. Route fetching (OSRM), turn-by-turn
announcements, and turn confirmation (via the REAL IMU) all run through
the exact same code path as a real GPS-equipped walk - so when the
audio says "turn left", you physically rotate the device and the real
IMU logic confirms it, exactly like the real thing.

This bypasses Nominatim text-based destination lookup (nav_engine's
normal DESTINATION_NAME flow) in favor of two literal coordinate pairs,
so the demo route is exact and doesn't depend on geocoding succeeding
at record time.
"""
import time
from osm_nav import GeoMath


def _step_towards(lat1, lon1, lat2, lon2, distance_m):
    """Move `distance_m` meters from (lat1, lon1) toward (lat2, lon2)."""
    total_dist = GeoMath.distance_in_meters(lat1, lon1, lat2, lon2)
    if total_dist < 1e-6 or distance_m >= total_dist:
        return lat2, lon2
    fraction = distance_m / total_dist
    new_lat = lat1 + (lat2 - lat1) * fraction
    new_lon = lon1 + (lon2 - lon1) * fraction
    return new_lat, new_lon


def simulate_navigation(
    nav_engine,
    start_lat,
    start_lon,
    dest_lat,
    dest_lon,
    step_meters=1.4,    # ~ average walking speed in meters/second
    interval_sec=1.0,   # how often a new simulated GPS point is sent
    max_steps=2000,
    route_wait_timeout=15,
):
    """
    Starts a simulated walk from (start_lat, start_lon) to
    (dest_lat, dest_lon), feeding nav_engine exactly the way real GPS
    would. Run this in its own thread - it blocks until arrival,
    timeout, or navigation stops.
    """
    # Skip Nominatim text lookup - use exact coordinates instead.
    nav_engine.dest_lat = dest_lat
    nav_engine.dest_lon = dest_lon
    nav_engine._resolve_destination_coords = lambda: True

    current_lat, current_lon = start_lat, start_lon
    nav_engine.update_live_gps(current_lat, current_lon)

    print("[Sim] Waiting for route to resolve...")
    wait_start = time.time()
    while not nav_engine.route_initialized:
        if time.time() - wait_start > route_wait_timeout:
            print(f"[Sim] Route failed to initialize within {route_wait_timeout}s - aborting.")
            return
        time.sleep(0.5)

    print("[Sim] Route initialized. Starting simulated walk...")

    for _ in range(max_steps):
        if not nav_engine.is_navigating or nav_engine.active_step is None:
            print("[Sim] Navigation ended (arrived or stopped).")
            return

        if nav_engine.waiting_for_turn:
            # Person is turning in place - simulated GPS position doesn't move.
            nav_engine.update_live_gps(current_lat, current_lon)
            time.sleep(interval_sec)
            continue

        target_lon, target_lat = nav_engine.active_step["maneuver"]["location"]
        current_lat, current_lon = _step_towards(
            current_lat, current_lon, target_lat, target_lon, step_meters
        )
        nav_engine.update_live_gps(current_lat, current_lon)
        time.sleep(interval_sec)

    print("[Sim] Max steps reached without arriving - stopping.")