#include <Arduino_RouterBridge.h>
#include <OneButton.h>   // Add this library

// ============================================================
// ULTRASONIC SENSORS (unchanged)
// ============================================================
const int trigPins[3] = {9, 7, 5};
const int echoPins[3] = {10, 8, 6};
long duration;
const float ALPHA = 0.6;
float filtered[3] = {-1.0, -1.0, -1.0};
const float THRESHOLD_CM = 50.0;
int previous_state[3] = {0, 0, 0};  // 0 = clear, 1 = obstacle
unsigned long previousMillis = 0;
const unsigned long SEND_INTERVAL_MS = 50;
OneButton button(3, true);
OneButton sos_but(4, true);
// ============================================================
// GPS MODULE – using hardware Serial1 (pins 0 and 1)
// ============================================================
// #define gpsSerial Serial1    // Use hardware UART 1
// TinyGPSPlus gps;
// float current_lat = 0.0;
// float current_lng = 0.0;
// bool gps_fixed = false;

// ============================================================
// FUNCTION PROTOTYPES
// ============================================================
float getDistance(int trig, int echo);
int checkObstacle(int sensor_index);
// void updateGPS();

// // RPC callbacks
// int getLeftStatus();
// int getCenterStatus();
// int getRightStatus();
int pingHandler();
// float getLatitude();
// float getLongitude();
// int getGpsFix();

// ============================================================
// SETUP
// ============================================================
void setup() {
  Bridge.begin();
  Serial.begin(9600);       // USB Serial Monitor (debugging)
  button.attachClick(shortClick);
  button.attachLongPressStop(longClick);
  sos_but.attachClick(shortClick_sos);
  sos_but.attachLongPressStop(longClick_sos);
  pinMode(4, INPUT_PULLUP);
  // ---- Ultrasonic pins ----
  for (int i = 0; i < 3; i++) {
    pinMode(trigPins[i], OUTPUT);
    pinMode(echoPins[i], INPUT);
    digitalWrite(trigPins[i], LOW);
  }

  // ---- GPS ----
  // gpsSerial.begin(9600);    // NEO‑6M default baud rate

  // // ---- Register RPC callbacks ----
  // Bridge.provide("read_left",    getLeftStatus);
  // Bridge.provide("read_center",  getCenterStatus);
  // Bridge.provide("read_right",   getRightStatus);
  Bridge.provide("ping",pingHandler);
  // Bridge.provide("get_lat",      getLatitude);
  // Bridge.provide("get_lng",      getLongitude);
  // Bridge.provide("get_gps_fix",  getGpsFix);
}

// ============================================================
// LOOP
// ============================================================
void loop() {
  Bridge.update();   
  button.tick();
  sos_but.tick();// Keep RPC bridge alive
  unsigned long currentMillis = millis();
  // Send data at exactly 20Hz without blocking
  if (currentMillis - previousMillis >= SEND_INTERVAL_MS) {
    previousMillis = currentMillis;

    // Read all three sensors
    int left = checkObstacle(0);
    int center = checkObstacle(1);
    int right = checkObstacle(2);

    // Pack as comma‑separated string
    String data = String(left) + "," + String(center) + "," + String(right);

    // Push to MPU
    Bridge.notify("ultrasonic", data);
  // ---- Update GPS (non‑blocking) ----
  // updateGPS();

  // (Optional) echo raw GPS data to Serial Monitor for debugging:
  // while (gpsSerial.available()) Serial.write(gpsSerial.read());

  //delay(10);
  }
}

void shortClick_sos() {
  Bridge.notify("SOS", "sos");
}

void longClick_sos() {
  Bridge.notify("SOS", "sos_cancel");
}

void shortClick() {
  Bridge.notify("Mul_Pur", "short");
}

void longClick() {
  Bridge.notify("Mul_Pur", "long");
}

// ============================================================
// ULTRASONIC FUNCTIONS (unchanged)
// ============================================================
float getDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  duration = pulseIn(echo, HIGH, 15000);   // 15ms timeout
  float raw_dist = (duration == 0) ? -1.0 : (duration * 0.034 / 2);

  int idx = (echo == echoPins[0]) ? 0 : (echo == echoPins[1]) ? 1 : 2;
  if (raw_dist > 0) {
    if (filtered[idx] < 0) filtered[idx] = raw_dist;
    else filtered[idx] = (ALPHA * raw_dist) + ((1 - ALPHA) * filtered[idx]);
    return filtered[idx];
  } else {
    filtered[idx] = -1.0;
    return -1.0;
  }
}

int checkObstacle(int sensor_index) {
  float dist = getDistance(trigPins[sensor_index], echoPins[sensor_index]);
  return (dist > 0 && dist < THRESHOLD_CM) ? 1 : 0;
}

// RPC callbacks for ultrasonic
// int getLeftStatus()   { return checkObstacle(0); }
// int getCenterStatus() { return checkObstacle(1); }
// int getRightStatus()  { return checkObstacle(2); }

// ============================================================
// GPS FUNCTIONS
// ============================================================
// void updateGPS() {
//   while (gpsSerial.available() > 0) {
//     char c = gpsSerial.read();
//     // Keep bridge alive even while parsing GPS
//     Bridge.update();
//     if (gps.encode(c)) {
//       if (gps.location.isValid()) {
//         current_lat = gps.location.lat();
//         current_lng = gps.location.lng();
//         gps_fixed = true;
//       } else {
//         gps_fixed = false;
//       }
//     }
//   }
// }

// RPC callbacks for GPS
// float getLatitude()  { return current_lat; }
// float getLongitude() { return current_lng; }
// int getGpsFix()      { return gps_fixed ? 1 : 0; }

// ============================================================
// SYSTEM CALLBACK
// ============================================================
int pingHandler() { return 1; }