#include <Arduino_RouterBridge.h>
#include <OneButton.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

// ============================================================
// ULTRASONIC SENSORS
// ============================================================
const int trigPins[3] = {9, 7, 5};
const int echoPins[3] = {10, 8, 6};
long duration;
const float ALPHA = 0.6;
float filtered[3] = {-1.0, -1.0, -1.0};
const float THRESHOLD_CM = 50.0;
unsigned long previousMillis = 0;
const unsigned long SEND_INTERVAL_MS = 50;

// ============================================================
// BUTTONS
// ============================================================
OneButton button(3, true);
OneButton sos_but(4, true);

// ============================================================
// IMU (MPU6050)
// ============================================================
Adafruit_MPU6050 mpu;
float heading = 0.0;
unsigned long lastIMURead = 0;
const unsigned long IMU_UPDATE_INTERVAL = 100;

// ============================================================
// FUNCTION PROTOTYPES
// ============================================================
float getDistance(int trig, int echo);
int checkObstacle(int sensor_index);
int pingHandler();
float getIMUHeading();
void initIMU();

String getAccelerometer() {
    sensors_event_t a, g, temp;
    mpu.getEvent(&a, &g, &temp);
    
    // These should already be in m/s²
    // But if they're not, apply scaling based on the configured range
    // For MPU6050_RANGE_8_G, divide by 4096 to get g, then multiply by 9.80665
    
    // Option A: Trust Adafruit library (recommended)
    char buffer[64];
    snprintf(buffer, sizeof(buffer), "%.2f,%.2f,%.2f", 
             a.acceleration.x, a.acceleration.y, a.acceleration.z);
    return String(buffer);
}

// ============================================================
// SETUP
// ============================================================
void setup() {
  Bridge.begin();
  Serial.begin(9600);
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);   // 8g range
  Bridge.provide("get_accel", getAccelerometer);
  // ---- Ultrasonic pins ----
  for (int i = 0; i < 3; i++) {
    pinMode(trigPins[i], OUTPUT);
    pinMode(echoPins[i], INPUT);
    digitalWrite(trigPins[i], LOW);
  }

  // ---- Buttons ----
  button.attachClick(shortClick);
  button.attachLongPressStop(longClick);
  sos_but.attachClick(shortClick_sos);
  sos_but.attachLongPressStop(longClick_sos);
  pinMode(4, INPUT_PULLUP);

  // ---- IMU ----
  initIMU();

  // ---- RPC Callbacks ----
  Bridge.provide("ping", pingHandler);
  Bridge.provide("get_heading", getIMUHeading);
}

// ============================================================
// LOOP
// ============================================================
void loop() {
  Bridge.update();
  button.tick();
  sos_but.tick();

  // ---- Ultrasonic (20Hz notify) ----
  unsigned long currentMillis = millis();
  if (currentMillis - previousMillis >= SEND_INTERVAL_MS) {
    previousMillis = currentMillis;

    int left = checkObstacle(0);
    int center = checkObstacle(1);
    int right = checkObstacle(2);

    String data = String(left) + "," + String(center) + "," + String(right);
    Bridge.notify("ultrasonic", data);
  }

  // ---- IMU heading is read on-demand via Bridge.call() ----
  // No continuous reading/notify – this is the request-response approach.
}

// ============================================================
// ULTRASONIC FUNCTIONS
// ============================================================
float getDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  duration = pulseIn(echo, HIGH, 15000);
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

// ============================================================
// IMU FUNCTIONS
// ============================================================
void initIMU() {
  if (!mpu.begin()) {
    Serial.println("MPU6050 not found!");
    return;
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
  Serial.println("MPU6050 ready.");
}

float getIMUHeading() {
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);

  static float yaw = 0.0;
  static unsigned long lastTime = micros();
  unsigned long currentTime = micros();
  float dt = (currentTime - lastTime) / 1000000.0;
  lastTime = currentTime;

  // Gyro Z in degrees per second (convert from rad/s)
  float gyroZ = g.gyro.z * 180.0 / PI;
  yaw += gyroZ * dt;

  // Normalize to 0-360
  if (yaw < 0) yaw += 360;
  if (yaw >= 360) yaw -= 360;

  return yaw;
}

// ============================================================
// BUTTON CALLBACKS
// ============================================================
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
// SYSTEM CALLBACKS
// ============================================================
int pingHandler() { return 1; }