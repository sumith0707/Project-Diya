import os
import time
import sys
import threading
import json
import cv2
import numpy as np
import sounddevice as sd
#print(sd.query_devices())
#print("Default input device:", sd.default.device[0])
# ========== Configuration ==========
BOARD_IP = "192.168.0.105"   # CHANGE to your board's actual IP
os.environ["ARDUINO_BOARD_IP"] = BOARD_IP

from arduino.app_utils import Bridge, App
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_imageclassification import VideoImageClassification
from osm_nav import OsmNavigationEngine
from emergency_manager import EmergencyManager
from voice_recognition import VoiceRecognition
from imu_module import IMUReader
from tts_manager import TTSManager

# ========== TTS Setup ==========
# Use absolute paths for reliability
BASE_DIR = "/app"  # Or use: os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
piper_bin = os.path.join(BASE_DIR, "piper", "piper")
model_path = os.path.join(BASE_DIR, "piper_voices", "en_US-lessac-medium.onnx")

try:
    tts = TTSManager(piper_bin=piper_bin, model_path=model_path, audio_device="plughw:1,0")
    print("TTS ready (Piper).")
except Exception as e:
    print(f"TTS initialization failed: {e}")
    tts = None

# from llm_manager import LLMManager

# # ========== LLM Setup ==========
# llm_manager = LLMManager()
# print("LLM Manager ready.")


sos_timer = None          # Timer object for delayed SOS
sos_active = False        # True while waiting for SOS confirmation

# ========== WebUI instance ==========
ui = WebUI()

# Hardcoded destination name string (e.g., "Majestic, Bengaluru")
DESTINATION_NAME = "Mangalore" 
nav_engine = OsmNavigationEngine(destination_name=DESTINATION_NAME)

# ---- Attach LLM to Navigation Engine ----
# nav_engine.set_llm_manager(llm_manager)
# print("[Main] LLM attached to navigation engine.")

# ========== IMU Setup ==========
try:
    imu = IMUReader()
    nav_engine.set_imu(imu)
    print("IMU ready for turn detection.")
except Exception as e:
    print(f"IMU initialization failed: {e}")
    nav_engine.set_imu(None)

# ========== Emergency Manager ==========
emergency = EmergencyManager(imu=imu)  # Pass IMU for fall detection
emergency.set_ui(ui)                    # Set UI for sending messages
emergency.start_fall_detection()        # Start fall detection thread

# ========== Shared GPS data (for future use) ==========
gps_data = {
    "lat": 0.0,
    "lng": 0.0,
    "fix": False,
    "last_update": 0.0
}
gps_lock = threading.Lock()

# ========== Voice Recognition Setup ==========

try:
    voice = VoiceRecognition(
        model_name="tiny",      # Use tiny model
        device="cpu",
        compute_type="int8",    # Optimized for CPU
        block_duration_ms=30,
        silence_timeout=1.5,
        vad_mode=1
    )
    print("Voice recognition engine ready (Faster Whisper tiny).")
except Exception as e:
    print(f"Failed to initialize Faster Whisper: {e}")
    voice = None

def speak(text):
    """Helper to speak text via TTS."""
    if tts is None:
        print(f"[TTS] Not available: {text}")
        return
    tts.speak_async(text)
    print(f"[TTS] Speaking: {text}")

speak("Helloe world")

# # ========== WebSocket handler for raw text ==========
def on_raw_text(sid, message):
    print(f"\n[Raw Text] Client {sid} sent: {message}")
    ui.send_message("reply", f"UNO Q received: {message}", sid)
    if message == "start":
        print("Nav starting")
        nav_engine.update_live_gps(lat2, lng2)
        print("Nav started")

# ========== WebSocket handler for GPS data ==========
def on_gps(sid, message):
    """
    Expects a JSON string: {"lat": 12.345, "lng": 67.890}
    """
    global lat2, lng2
    try:
        # If message is a string, parse it as JSON
        if isinstance(message, str):
            data = json.loads(message)
        else:
            data = message

        lat = float(data.get("lat", 0))
        lng = float(data.get("lng", 0))

        # Validate coordinates (optional)
        # if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        #     print(f"\n[GPS] Invalid coordinates from {sid}: lat={lat}, lng={lng}")
        #     return

        with gps_lock:
            gps_data["lat"] = lat
            gps_data["lng"] = lng
            gps_data["fix"] = True
            gps_data["last_update"] = time.time()

        

        print(f"\n[GPS] {sid} -> Lat: {lat:.6f}, Lng: {lng:.6f}")
        lat2=lat
        lng2=lng
        # Optional: send acknowledgment
        # ui.send_message("gps_ack", {"status": "ok", "lat": lat, "lng": lng}, sid)

    except json.JSONDecodeError:
        print(f"\n[GPS] Invalid JSON from {sid}: {message}")
    except Exception as e:
        print(f"\n[GPS] Error: {e}")

# ============================================================
# Currency Detection
# ============================================================
# ============================================================
# Currency Detection (Based on Working Example)
# ============================================================
class CurrencyDetector:
    def __init__(self, confidence_threshold=0.5):
        self.classifier = VideoImageClassification(confidence=confidence_threshold, debounce_sec=0.0)
        self.confidence_threshold = confidence_threshold
        self.is_running = False
        self.last_label = None
        self.detection_start_time = None
        self.detection_duration = 2.0
        self.callback = None

        # ---- Register the callback using a wrapper function ----
        # The brick expects a plain function, not a bound method.
        def process_wrapper(classifications):
            self._process_results(classifications)

        self.classifier.on_detect_all(process_wrapper)
        print("[Currency] Detector ready. Callback registered.")

    def start(self, callback=None):
        if self.is_running:
            print("[Currency] Already running.")
            return
        self.is_running = True
        self.callback = callback
        self.last_label = None
        self.detection_start_time = None
        print("[Currency] Detection started. Hold note in front of camera.")

    def stop(self):
        self.is_running = False
        self.last_label = None
        self.detection_start_time = None
        print("[Currency] Detection stopped.")

    def _process_results(self, classifications: dict):
        """Called by the brick on every frame (via wrapper)."""
        if not self.is_running:
            return

        if not classifications:
            self.last_label = None
            self.detection_start_time = None
            return

        # Find best label
        best_label = None
        best_confidence = 0
        for label, confidence in classifications.items():
            if confidence > best_confidence:
                best_confidence = confidence
                best_label = label

        # Debug print
        print(f"[Currency] All: {classifications}")

        if best_confidence < self.confidence_threshold:
            self.last_label = None
            self.detection_start_time = None
            return

        now = time.time()

        if best_label == self.last_label:
            if self.detection_start_time is None:
                self.detection_start_time = now
                print(f"[Currency] First detection: {best_label} ({best_confidence:.2f})")
            elif now - self.detection_start_time >= self.detection_duration:
                print(f"[Currency] CONFIRMED: {best_label} (held for {now - self.detection_start_time:.1f}s)")
                self.is_running = False
                self.last_label = None
                self.detection_start_time = None
                if self.callback:
                    self.callback(best_label)
        else:
            self.last_label = best_label
            self.detection_start_time = now
            print(f"[Currency] New label: {best_label} ({best_confidence:.2f})")
            
# ========== Currency Detector ==========
currency_detector = CurrencyDetector(confidence_threshold=0.70)

def on_currency_detected(label):
    print(f"[Currency] CONFIRMED: {label}")
    speak(f"This is a {label} note")

    # ---- Send to LLM for natural response ----
    # actions = llm_manager.process_currency_detection(label, confidence=0.85)

    # if actions.get("speak"):
    #     ui.send_message("tts", actions["speak"])
    #     print(f"[LLM] Speaking: {actions['speak']}")

# ========== Register handlers ==========
ui.on_message("raw_text", on_raw_text)
ui.on_message("gps", on_gps)

obstacle_data = {"left": 0, "center": 0, "right": 0}
obstacle_lock = threading.Lock()

# ========== Handler for ultrasonic notifications ==========
def on_ultrasonic(data):
    """Called when MCU pushes ultrasonic data via Bridge.notify()"""
    try:
        # Data is a comma-separated string: "1,0,1"
        parts = data.split(",")
        if len(parts) == 3:
            left = int(parts[0])
            center = int(parts[1])
            right = int(parts[2])
            with obstacle_lock:
                obstacle_data["left"] = left
                obstacle_data["center"] = center
                obstacle_data["right"] = right
            # Optional: print only on change
            # print(f"Ultrasonic: L={left}, C={center}, R={right}")
    except Exception as e:
        print(f"Ultrasonic parse error: {e}")

Bridge.provide("ultrasonic", on_ultrasonic)

# ========== Obstacle monitoring (runs in a background thread) ==========
def monitor_obstacles(update_interval=0.05):
    print("Obstacle monitor running (using Bridge.notify()).")
    prev_state = (-1, -1, -1)

    try:
        while True:
            with obstacle_lock:
                left = obstacle_data["left"]
                center = obstacle_data["center"]
                right = obstacle_data["right"]

            if (left, center, right) != prev_state:
                status = ""
                status += "L" if left else "-"
                status += "C" if center else "-"
                status += "R" if right else "-"
                timestamp = time.strftime("%H:%M:%S")
                print(f"[{timestamp}] Obstacles: [{status}]")
                prev_state = (left, center, right)

            time.sleep(update_interval)
    except KeyboardInterrupt:
        print("Obstacle monitor stopped.")

# def send_sos():
#     """Called after 5 seconds if SOS is not cancelled."""
#     global sos_active, sos_timer
#     print("[SOS] Timer expired – sending SOS alert!")
#     ui.send_message("sos", "SOS")   # Broadcast to all clients
#     sos_active = False
#     sos_timer = None

# # ========== SOS Handler ==========
# def on_sos(state):
#     global sos_active, sos_timer
#     print(f"[SOS] Received state: {state}")

#     if state == "sos":
#         # Short press – start timer
#         if sos_active:
#             # Cancel any existing timer (shouldn't happen normally)
#             if sos_timer:
#                 sos_timer.cancel()
#                 sos_timer = None
#         # Start new 5-second timer
#         sos_active = True
#         sos_timer = threading.Timer(5.0, send_sos)
#         sos_timer.start()
#         print("[SOS] Timer started – waiting 5 seconds for cancellation.")

#     elif state == "sos_cancel":
#         # Long press – cancel if timer is active
#         if sos_active:
#             if sos_timer:
#                 sos_timer.cancel()
#                 sos_timer = None
#             sos_active = False
#             print("[SOS] Cancelled – no alert sent.")
#             ui.send_message("sos", "SOS_CANCELLED")   # Optional: inform UI
#         else:
#             # Long press without active timer – ignore
#             print("[SOS] Long press ignored (no active SOS).")

def on_sos(state):
    if state == "sos":
        emergency.trigger_emergency(source="button")
    elif state == "sos_cancel":
        emergency.cancel_emergency()

def handle_voice_result(text):
    print(f"[Voice Result] {text}")
    # ---- Currency detection ----
    if "detect money" in text.lower() or "identify money" in text.lower():
        print("Starting currency detection...")
        currency_detector.start(callback=on_currency_detected)
        return

    # ---- Unknown command ----
    speak("I didn't understand that command.")

    # ---- Process voice command through LLM ----
    # actions = llm_manager.process_voice_command(text)

    # # ---- Execute actions ----
    # if actions.get("speak"):
    #     ui.send_message("tts", actions["speak"])
    #     print(f"[LLM] Speaking: {actions['speak']}")

    # if actions.get("action") == "currency":
    #     print("[LLM] Starting currency detection...")
    #     currency_detector.start(callback=on_currency_detected)

    # if actions.get("action") == "navigate":
    #     destination = actions.get("destination")
    #     if destination:
    #         nav_engine.destination_name = destination
    #         nav_engine._resolve_destination_coords()
    #         nav_engine.update_live_gps(lat2, lng2)
    #         ui.send_message("tts", f"Navigating to {destination}")
    #         print(f"[LLM] Navigating to {destination}")

    # if actions.get("action") == "reroute":
    #     nav_engine.reroute()

def on_mul(state):
    print(f"[Mul_Pur] Button pressed: {state}")

    # ---- If currency detection is running, cancel it ----
    if currency_detector.is_running:
        if state == "long":
            print("Cancelling currency detection...")
            currency_detector.stop()
            print("[Currency] Cancelled by user.")
        return

    # ---- Voice recognition ----
    if voice is None:
        print("Voice recognition not available.")
        return
    if state == "short":
        print("Starting voice recognition...")
        voice.start_recording(callback=handle_voice_result)
    else:  # long press
        print("Cancelling voice recognition...")
        voice.stop_recording()
    
Bridge.provide("SOS", on_sos)
Bridge.provide("Mul_Pur", on_mul)

# ========== Main entry point ==========
def main():
    print("=" * 50)
    print("Diya 2 – Obstacle Detection + Raw Text + GPS")
    print("=" * 50)

    obstacle_thread = threading.Thread(target=monitor_obstacles, daemon=True)
    obstacle_thread.start()
    time.sleep(2)

    App.run()

if __name__ == "__main__":
    main()