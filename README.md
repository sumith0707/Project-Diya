# Project Diya
### An AI-Powered "Digital Eye" and Haptic Navigation System for the Visually Impaired

Project Diya is a wearable AI device that acts as a digital eye — detecting obstacles, faces, and currency on the user's behalf — while guiding them turn-by-turn through spoken directions and vibration cues. Built on the Arduino Uno Q, it combines real-time on-device sensing with edge AI vision to give visually impaired users greater situational awareness and independent mobility.

---

## Features

- **Obstacle detection & haptic feedback** — three ultrasonic sensors (left/center/right) continuously scan for obstacles. Vibration motors pulse in the direction that's clear to walk, and vibrate continuously on all three when the path is fully blocked.
- **GPS turn-by-turn navigation** — routes calculated via OpenStreetMap/OSRM, with spoken turn instructions and real IMU-based turn confirmation (the device detects when you've actually turned, not just that time has passed).
- **Face tracking & recognition** — a pan-tilt camera physically follows a detected face; users can ask "who's approaching?" or enroll new faces by voice command.
- **Currency detection** — hold a note in front of the camera and Diya identifies the denomination via an on-device classification model, announced by voice.
- **"What's around me" scene scan** — a servo-driven camera sweep detects and describes nearby objects.
- **Voice command interface** — a single button triggers speech recognition for hands-free control of the above features.
- **SOS / fall detection** — a dedicated button and IMU-based fall detection trigger an emergency alert flow.

---

## Hardware

| Component | Purpose | Connected To |
|---|---|---|
| Arduino Uno Q | Main controller (real-time I/O + Linux-side AI/vision) | — |
| 3× HC-SR04 ultrasonic sensors | Obstacle detection (left/center/right) | D2/D5, D6/D7, D8/D9 |
| 2× MG90S servo motors | Pan-tilt camera mount | D11 (pan), D10 (tilt) |
| 3× Vibration motor modules | Directional haptic feedback | A0, A1, A2 |
| MPU6050 (GY-521) | IMU — turn confirmation & fall detection | A4 (SDA), A5 (SCL) |
| 2× Push buttons | Multi-purpose control / SOS | D3, D4 |
| USB camera | Vision input (face/object/currency detection) | USB |
| USB/audio speaker | Text-to-speech output | USB / audio out |

See `docs/schematic.png` for the full wiring diagram (built in Wokwi, using the standard Arduino Uno board as a pin-compatible stand-in since Wokwi does not yet support the Uno Q).

---

## Software Architecture

- **`sketch.ino`** — runs on the Uno Q's microcontroller side. Handles ultrasonic sensing (interrupt-driven, non-blocking state machine), servo control, IMU reading with continuous yaw integration, vibration motor output, and button input. Exposes all of this to the Linux side via `Bridge` RPC calls (`servo`, `vibrate`, `get_heading`, `get_accel`, `ultrasonic`, `SOS`, `Mul_Pur`).

- **`main.py`** — the Linux-side application entry point. Boots the shared camera, TTS engine, WebUI, and all detection "bricks" (object detection, currency classification, face recognition, YOLOX scanner), and wires together voice commands, button events, and sensor data into the overall behavior described above.

- **`osm_nav.py`** — `OsmNavigationEngine`: resolves a destination via Nominatim, fetches a walking route from OSRM, tracks progress toward each turn using live GPS, and confirms turns using real IMU heading changes (with callback hooks for vibration cues).

- **`vibration_manager.py`** — drives the 3 vibration motors based on live obstacle sensor state (pulsed/continuous patterns) and overlays a distinct double-pulse pattern for upcoming navigation turns.

- **`nav_simulator.py`** — a testing/demo utility that feeds simulated GPS coordinates into `OsmNavigationEngine`, so the full navigation flow (route fetch, turn announcements, IMU turn confirmation) can be exercised and filmed without physically walking an outdoor route. See in-file docstring for usage.

- Additional modules referenced but not detailed here: `face_recognition_manager.py`, `emergency_manager.py`, `voice_recognition.py`, `imu_module.py`, `tts_manager.py`, `yolox_detector.py` — see each file's docstring for specifics.

---

## Setup

1. Flash `sketch.ino` to the Uno Q's microcontroller side via the Arduino IDE / Arduino CLI.
2. On the Linux side, install dependencies (Arduino App Lab bricks, `opencv-python`, `sounddevice`, `requests`, plus whatever your `voice_recognition.py`/`tts_manager.py` require — see their respective files).
3. Set `BOARD_IP` in `main.py` to your Uno Q's IP address.
4. Place Piper TTS binaries/voice models under `/app/piper` and `/app/piper_voices` (or update the paths in `main.py`).
5. Run:
   ```
   python3 main.py
   ```

## Running the Navigation Demo (Simulated GPS)

Since outdoor GPS testing isn't always practical, `nav_simulator.py` lets you demo the full navigation flow indoors using two literal coordinate pairs instead of physically walking:

1. Edit `SIM_START_LAT/LON` and `SIM_DEST_LAT/LON` near the top of `main.py`.
2. Start the app, open the WebUI text input, and send `simulate`.
3. GPS position is simulated, but **IMU turn confirmation is real** — physically rotate the device when prompted to advance the route.

---

## Known Limitations

- GPS navigation has primarily been validated using the simulated GPS flow above; outdoor field testing is ongoing.
- Currency and object detection accuracy depends on lighting and camera positioning.
- Requires an internet connection for route resolution (Nominatim + OSRM).

## License

MIT License — see [`LICENSE`](./LICENSE) for full text.

## Acknowledgments

Built on the Arduino Uno Q and Arduino App Lab bricks framework, with on-device inference via Edge Impulse models, offline text-to-speech via Piper, and routing via OpenStreetMap Nominatim/OSRM.
