import os
import time
import sys
import threading
import json
import cv2
import numpy as np
import sounddevice as sd
from datetime import datetime, UTC

# ========== Configuration ==========
BOARD_IP = "192.168.0.106"
os.environ["ARDUINO_BOARD_IP"] = BOARD_IP

from arduino.app_utils import Bridge, App
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_imageclassification import VideoImageClassification
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_peripherals.camera import Camera
from osm_nav import OsmNavigationEngine
from emergency_manager import EmergencyManager
from voice_recognition import VoiceRecognition
from imu_module import IMUReader
from tts_manager import TTSManager

# ============================================================
# TTS Setup
# ============================================================
BASE_DIR = "/app"
piper_bin = os.path.join(BASE_DIR, "piper", "piper")
model_path = os.path.join(BASE_DIR, "piper_voices", "en_US-lessac-medium.onnx")

try:
    tts = TTSManager(piper_bin=piper_bin, model_path=model_path, audio_device="plughw:1,0")
    print("TTS ready (Piper).")
except Exception as e:
    print(f"TTS initialization failed: {e}")
    tts = None

def speak(text):
    """Helper to speak text via TTS."""
    if tts is None:
        print(f"[TTS] Not available: {text}")
        return
    tts.speak_async(text)
    print(f"[TTS] Speaking: {text}")

# ============================================================
# WebUI
# ============================================================
ui = WebUI()

# ============================================================
# Shared Camera
# ============================================================
# A single USBCamera instance shared by every video brick in this app.
# VideoObjectDetection and VideoImageClassification each open their own
# exclusive V4L2 capture session when no `camera` is passed in, and most
# USB webcams only support one exclusive capture session at a time. Passing
# the same USBCamera instance into both bricks avoids that contention.
shared_camera = Camera(source=0, resolution=(640, 480), fps=10)
shared_camera.start()

# ============================================================
# Navigation Engine
# ============================================================
DESTINATION_NAME = "Mangalore"
nav_engine = OsmNavigationEngine(destination_name=DESTINATION_NAME)

# ============================================================
# IMU Setup
# ============================================================
try:
    imu = IMUReader()
    nav_engine.set_imu(imu)
    print("IMU ready for turn detection.")
except Exception as e:
    print(f"IMU initialization failed: {e}")
    nav_engine.set_imu(None)

# ============================================================
# Emergency Manager
# ============================================================
emergency = EmergencyManager(imu=imu)
emergency.set_ui(ui)
emergency.start_fall_detection()

# ============================================================
# GPS Data
# ============================================================
gps_data = {"lat": 0.0, "lng": 0.0, "fix": False, "last_update": 0.0}
gps_lock = threading.Lock()
lat2 = 0.0
lng2 = 0.0

# ============================================================
# Voice Recognition
# ============================================================
try:
    voice = VoiceRecognition(
        model_name="tiny",
        device="cpu",
        compute_type="int8",
        block_duration_ms=30,
        silence_timeout=1.5,
        vad_mode=1
    )
    print("Voice recognition engine ready (Faster Whisper tiny).")
except Exception as e:
    print(f"Failed to initialize Faster Whisper: {e}")
    voice = None

# ============================================================
# Currency Detector (Existing – Unchanged, now uses shared camera)
# ============================================================
class CurrencyDetector:
    def __init__(self, confidence_threshold=0.5, camera=None):
        # The brick itself is NOT constructed here. It's created fresh each
        # time currency-detection mode starts, and torn down when it ends,
        # so the classification runner only receives frames and runs
        # inference while this mode is actually active.
        self.camera = camera
        self.confidence_threshold = confidence_threshold
        self.classifier = None
        self.is_running = False
        self.last_label = None
        self.detection_start_time = None
        self.detection_duration = 2.0
        self.callback = None
        print("[Currency] Detector ready (lazy - brick not yet started).")

    def start(self, callback=None):
        if self.is_running:
            print("[Currency] Already running.")
            return

        # Construct the brick on demand.
        self.classifier = VideoImageClassification(
            camera=self.camera,
            confidence=self.confidence_threshold,
            debounce_sec=0.0,
        )

        def process_wrapper(classifications):
            self._process_results(classifications)

        self.classifier.on_detect_all(process_wrapper)
        self.classifier.start()

        self.is_running = True
        self.callback = callback
        self.last_label = None
        self.detection_start_time = None
        print("[Currency] Detection started. Hold note in front of camera.")

    def stop(self):
        self.is_running = False
        self.last_label = None
        self.detection_start_time = None

        if self.classifier is not None:
            classifier_to_stop = self.classifier
            self.classifier = None
            try:
                classifier_to_stop.stop()
            except Exception as e:
                print(f"[Currency] Error stopping classifier: {e}")

        print("[Currency] Detection stopped.")

    def _stop_async(self):
        """Tear down the classifier from a separate thread.

        Used when triggered from inside the classifier's own callback
        (see _process_results) to avoid the callback thread blocking on
        its own brick's stop()/join() call.
        """
        threading.Thread(target=self.stop, daemon=True).start()

    def _process_results(self, classifications: dict):
        if not self.is_running:
            return
        if not classifications:
            self.last_label = None
            self.detection_start_time = None
            return

        best_label = None
        best_confidence = 0
        for label, confidence in classifications.items():
            if confidence > best_confidence:
                best_confidence = confidence
                best_label = label

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
                callback = self.callback
                # Tear down the brick asynchronously - we're currently
                # running inside its own callback thread.
                self._stop_async()
                if callback:
                    callback(best_label)
        else:
            self.last_label = best_label
            self.detection_start_time = now
            print(f"[Currency] New label: {best_label} ({best_confidence:.2f})")

currency_detector = CurrencyDetector(confidence_threshold=0.70, camera=shared_camera)

def on_currency_detected(label):
    print(f"[Currency] CONFIRMED: {label}")
    speak(f"This is a {label} note")
    resume_object_detection()

# ============================================================
# OBJECT DETECTION MANAGER (Merged from test project, now uses shared camera)
# ============================================================
class ObjectDetectionManager:
    def __init__(self, camera=None):
        self.detection_stream = VideoObjectDetection(
            camera=camera,
            confidence=0.2,
            debounce_sec=0.0,
        )

        # Frame & Servo Boundaries
        self.FRAME_WIDTH = 640
        self.FRAME_HEIGHT = 480
        self.FRAME_CENTER_X = self.FRAME_WIDTH // 2
        self.FRAME_CENTER_Y = self.FRAME_HEIGHT // 2

        self.PAN_MIN = 20
        self.PAN_MAX = 160
        self.TILT_MIN = 20
        self.TILT_MAX = 160

        self.DEAD_ZONE_X = 25
        self.DEAD_ZONE_Y = 25
        self.KP_PAN = 0.04
        self.KP_TILT = 0.04

        self.current_pan_angle = 90.0
        self.current_tilt_angle = 90.0

        self.tracking_enabled = True
        self.last_detection_time = time.time()
        self.DETECTION_TIMEOUT = 7.0
        self.RECENTER_EASE = 0.1

        # Object Lock
        self.LOCK_DURATION = 5.0
        self.locked_label = None
        self.lock_start_time = 0.0

        # Threading
        self.latest_center = None
        self.has_new_target = False
        self.target_lock = threading.Lock()
        self.servo_thread = None
        self.running = False
        self.paused = False

        # Register WebUI handlers
        ui.on_message("override_th", self.on_override_threshold)
        ui.on_message("toggle_tracking", self.on_toggle_tracking)

        self.last_sent_pan = -1
        self.last_sent_tilt = -1

        # ========== FIX: Use a wrapper function for the callback ==========
        def detection_wrapper(detections):
            self.send_detections_to_ui(detections)

        self.detection_stream.on_detect_all(detection_wrapper)

    def on_override_threshold(self, sid, threshold):
        try:
            self.detection_stream.override_threshold(float(threshold))
            print(f"[ObjectDetect] Threshold updated to {threshold}")
        except Exception as e:
            print(f"[ObjectDetect] Error: {e}")

    def on_toggle_tracking(self, sid, state):
        self.tracking_enabled = (state == "on")
        print(f"[ObjectDetect] Tracking {'enabled' if self.tracking_enabled else 'disabled'}.")
        if not self.tracking_enabled:
            self.locked_label = None
            self.current_pan_angle = 90.0
            self.current_tilt_angle = 90.0
            self.set_servo_target(90, 90)

    def set_servo_target(self, pan, tilt):
        # Convert float angles to rounded integers
        pan = max(self.PAN_MIN, min(self.PAN_MAX, int(round(pan))))
        tilt = max(self.TILT_MIN, min(self.TILT_MAX, int(round(tilt))))

        # 2. MODIFY THIS: Only fire RPC when an actual degree step occurs
        if pan != self.last_sent_pan or tilt != self.last_sent_tilt:
            try:
                Bridge.notify("servo", f"{pan},{tilt}")
                self.last_sent_pan = pan
                self.last_sent_tilt = tilt
            except Exception as e:
                print(f"[ObjectDetect] Servo error: {e}")

    def send_detections_to_ui(self, detections: dict):
        # If paused, ignore detections
        if self.paused:
            return

        best_center = None
        now = time.time()

        # Forward raw detections to WebUI
        if detections:
            for key, values in detections.items():
                for value in values:
                    entry = {
                        "content": key,
                        "confidence": value.get("confidence"),
                        "timestamp": datetime.now(UTC).isoformat()
                    }
                    ui.send_message("detection", message=entry)

        # Target Selection with 5-Second Lock
        lock_active = (self.locked_label is not None) and ((now - self.lock_start_time) < self.LOCK_DURATION)

        if lock_active:
            if detections and self.locked_label in detections:
                best_dist = float('inf')
                for det in detections[self.locked_label]:
                    bbox = det.get("bounding_box_xyxy", [0, 0, 0, 0])
                    if bbox != [0, 0, 0, 0] and len(bbox) >= 4:
                        x1, y1, x2, y2 = bbox
                        cx = (x1 + x2) / 2.0
                        cy = (y1 + y2) / 2.0
                        dist = ((cx - self.FRAME_CENTER_X) ** 2 + (cy - self.FRAME_CENTER_Y) ** 2) ** 0.5
                        if dist < best_dist:
                            best_dist = dist
                            best_center = (cx, cy)
        else:
            if detections:
                highest_conf = -1.0
                selected_label = None
                selected_center = None

                for label, values in detections.items():
                    for det in values:
                        conf = det.get("confidence", 0.0) or 0.0
                        bbox = det.get("bounding_box_xyxy", [0, 0, 0, 0])
                        if bbox != [0, 0, 0, 0] and len(bbox) >= 4:
                            if conf > highest_conf:
                                highest_conf = conf
                                selected_label = label
                                x1, y1, x2, y2 = bbox
                                selected_center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

                if selected_label is not None:
                    self.locked_label = selected_label
                    self.lock_start_time = now
                    best_center = selected_center
                    print(f"[ObjectDetect] Locked onto '{self.locked_label}' (Conf: {highest_conf:.2f}) for 5s.")
            else:
                self.locked_label = None

        with self.target_lock:
            if best_center is not None:
                self.latest_center = best_center
                self.has_new_target = True

    def servo_control_loop(self):
        while self.running:
            # If paused, skip updating servos
            if self.paused:
                time.sleep(0.05)
                continue

            with self.target_lock:
                center = self.latest_center
                new_target = self.has_new_target
                self.has_new_target = False

            if self.tracking_enabled and new_target and center is not None:
                cx, cy = center

                error_x = cx - self.FRAME_CENTER_X
                error_y = cy - self.FRAME_CENTER_Y

                if abs(error_x) < self.DEAD_ZONE_X:
                    error_x = 0
                if abs(error_y) < self.DEAD_ZONE_Y:
                    error_y = 0

                self.current_pan_angle -= error_x * self.KP_PAN
                self.current_tilt_angle -= error_y * self.KP_TILT

                self.current_pan_angle = max(self.PAN_MIN, min(self.PAN_MAX, self.current_pan_angle))
                self.current_tilt_angle = max(self.TILT_MIN, min(self.TILT_MAX, self.current_tilt_angle))

                self.last_detection_time = time.time()
                self.set_servo_target(self.current_pan_angle, self.current_tilt_angle)

            else:
                time_since_detection = time.time() - self.last_detection_time

                if self.tracking_enabled and (time_since_detection > self.DETECTION_TIMEOUT):
                    pan_diff = 90.0 - self.current_pan_angle
                    tilt_diff = 90.0 - self.current_tilt_angle

                    if abs(pan_diff) > 0.5 or abs(tilt_diff) > 0.5:
                        self.current_pan_angle += pan_diff * self.RECENTER_EASE
                        self.current_tilt_angle += tilt_diff * self.RECENTER_EASE
                        self.set_servo_target(self.current_pan_angle, self.current_tilt_angle)
                    elif self.current_pan_angle != 90.0 or self.current_tilt_angle != 90.0:
                        self.current_pan_angle = 90.0
                        self.current_tilt_angle = 90.0
                        self.set_servo_target(90, 90)

            time.sleep(0.033)  # ~30 Hz

    def start(self):
        if self.running:
            return
        self.running = True
        self.paused = False
        self.servo_thread = threading.Thread(target=self.servo_control_loop, daemon=True)
        self.servo_thread.start()
        print("[ObjectDetect] Started.")

    def pause(self):
        self.paused = True
        print("[ObjectDetect] Paused.")

    def resume(self):
        self.paused = False
        self.last_detection_time = time.time()
        print("[ObjectDetect] Resumed.")

    def stop(self):
        self.running = False
        print("[ObjectDetect] Stopped.")

# ============================================================
# Instantiate Object Detection Manager
# ============================================================
object_detector = ObjectDetectionManager(camera=shared_camera)
object_detector.start()

# ============================================================
# Mode Management Functions
# ============================================================
def pause_object_detection():
    object_detector.pause()
    print("[Mode] Object detection paused.")

def resume_object_detection():
    object_detector.resume()
    print("[Mode] Object detection resumed.")

# ============================================================
# WebSocket Handlers (Existing)
# ============================================================
def on_raw_text(sid, message):
    print(f"\n[Raw Text] Client {sid} sent: {message}")
    ui.send_message("reply", f"UNO Q received: {message}", sid)
    if message == "start":
        print("Nav starting")
        nav_engine.update_live_gps(lat2, lng2)
        print("Nav started")

def on_gps(sid, message):
    global lat2, lng2
    try:
        if isinstance(message, str):
            data = json.loads(message)
        else:
            data = message

        lat = float(data.get("lat", 0))
        lng = float(data.get("lng", 0))

        with gps_lock:
            gps_data["lat"] = lat
            gps_data["lng"] = lng
            gps_data["fix"] = True
            gps_data["last_update"] = time.time()

        print(f"\n[GPS] {sid} -> Lat: {lat:.6f}, Lng: {lng:.6f}")
        lat2 = lat
        lng2 = lng

    except json.JSONDecodeError:
        print(f"\n[GPS] Invalid JSON from {sid}: {message}")
    except Exception as e:
        print(f"\n[GPS] Error: {e}")

# ============================================================
# Ultrasonic Handler
# ============================================================
obstacle_data = {"left": 0, "center": 0, "right": 0}
obstacle_lock = threading.Lock()

def on_ultrasonic(data):
    try:
        parts = data.split(",")
        if len(parts) == 3:
            left = int(parts[0])
            center = int(parts[1])
            right = int(parts[2])
            with obstacle_lock:
                obstacle_data["left"] = left
                obstacle_data["center"] = center
                obstacle_data["right"] = right
    except Exception as e:
        print(f"Ultrasonic parse error: {e}")

Bridge.provide("ultrasonic", on_ultrasonic)

# ============================================================
# Obstacle Monitoring
# ============================================================
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

# ============================================================
# SOS & Multi-Purpose Button Handlers
# ============================================================
def on_sos(state):
    if state == "sos":
        emergency.trigger_emergency(source="button")
    elif state == "sos_cancel":
        emergency.cancel_emergency()

def handle_voice_result(text):
    print(f"[Voice Result] {text}")

    # ---- Currency Detection ----
    if "detect money" in text.lower() or "identify money" in text.lower():
        print("Starting currency detection...")
        pause_object_detection()
        currency_detector.start(callback=on_currency_detected)
        return

    # ---- Unknown command ----
    speak("I didn't understand that command.")

def on_mul(state):
    print(f"[Mul_Pur] Button pressed: {state}")

    # ---- If currency detection is running, cancel it ----
    if currency_detector.is_running:
        if state == "long":
            print("Cancelling currency detection...")
            currency_detector.stop()
            resume_object_detection()
            speak("Currency detection cancelled.")
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

# ============================================================
# Register RPC Handlers
# ============================================================
Bridge.provide("SOS", on_sos)
Bridge.provide("Mul_Pur", on_mul)

# ============================================================
# WebUI Handlers
# ============================================================
ui.on_message("raw_text", on_raw_text)
ui.on_message("gps", on_gps)

# ============================================================
# Main Entry Point
# ============================================================
def main():
    print("=" * 50)
    print("Diya 2 – Navigation + Vision Assistant")
    print("=" * 50)

    obstacle_thread = threading.Thread(target=monitor_obstacles, daemon=True)
    obstacle_thread.start()
    time.sleep(2)

    App.run()

if __name__ == "__main__":
    main()