#include <Arduino_RouterBridge.h>
#include <OneButton.h>
#include <SWI2C.h>
#include <Servo.h>  // NEW

// ============================================================
// SERVO CONFIGURATION (NEW)
// ============================================================
Servo servo_pan;
Servo servo_tilt;

const int PAN_PIN = 11;
const int TILT_PIN = 10;

const int PAN_MIN = 20;
const int PAN_MAX = 160;
const int TILT_MIN = 20;
const int TILT_MAX = 160;

float current_pan = 90.0;
float current_tilt = 90.0;
float target_pan = 90.0;
float target_tilt = 90.0;

const float EASE_FACTOR = 0.30;

// ============================================================
// SWI2C CONFIGURATION (UNCHANGED)
// ============================================================
#define SDA_PIN A4
#define SCL_PIN A5
#define MPU6050_ADDR 0x68

SWI2C mpu(SDA_PIN, SCL_PIN, MPU6050_ADDR);

// ============================================================
// MPU6050 REGISTERS (UNCHANGED)
// ============================================================
#define MPU6050_ACCEL_XOUT_H 0x3B
#define MPU6050_GYRO_XOUT_H  0x43
#define MPU6050_PWR_MGMT_1   0x6B
#define MPU6050_ACCEL_CONFIG 0x1C
#define MPU6050_GYRO_CONFIG  0x1B

// ============================================================
// SCALING FACTORS (UNCHANGED)
// ============================================================
#define ACCEL_SCALE (4096.0 / 9.80665)  // 417.6 LSB per m/s²
#define GYRO_SCALE 65.5                 // LSB per °/s

// ============================================================
// ULTRASONIC SENSORS (UNCHANGED)
// ============================================================
const int trigPins[3] = {9, 7, 5};
const int echoPins[3] = {2, 8, 6};
long duration;
const float ALPHA = 0.6;
float filtered[3] = {-1.0, -1.0, -1.0};
const float THRESHOLD_CM = 50.0;
unsigned long previousMillis = 0;
const unsigned long SEND_INTERVAL_MS = 50;

// ============================================================
// BUTTONS (UNCHANGED)
// ============================================================
OneButton button(3, true);
OneButton sos_but(4, true);

// ============================================================
// MPU CACHE (UNCHANGED)
// ============================================================
float cached_ax, cached_ay, cached_az;
float cached_gx, cached_gy, cached_gz;
bool imu_initialized = false;

unsigned long lastMPURead = 0;
const unsigned long MPU_READ_INTERVAL = 100;  // 20 Hz

// ============================================================
// FUNCTION PROTOTYPES
// ============================================================
float getDistance(int trig, int echo);
int checkObstacle(int sensor_index);
int pingHandler();
float getIMUHeading();
String getAccelerometer();
bool initMPU6050();
void readMPU6050();
void onServoCommand(String data);  // NEW

// ============================================================
// SETUP
// ============================================================
void setup() {
  Bridge.begin();
  Serial.begin(9600);

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

  // ---- MPU6050 ----
  if (initMPU6050()) {
    Serial.println("MPU6050 ready (SWI2C).");
    readMPU6050();
  } else {
    Serial.println("MPU6050 not found!");
  }

  // ---- Servos (NEW) ----
  servo_pan.attach(PAN_PIN);
  servo_tilt.attach(TILT_PIN);
  servo_pan.write(90);
  servo_tilt.write(90);

  // ---- RPC Callbacks ----
  Bridge.provide("ping", pingHandler);
  Bridge.provide("get_heading", getIMUHeading);
  Bridge.provide("get_accel", getAccelerometer);
  Bridge.provide("servo", onServoCommand);  // NEW
}

// ============================================================
// LOOP
// ============================================================
void loop() {
  Bridge.update();
  button.tick();
  sos_but.tick();

  unsigned long currentMillis = millis();

  // ---- MPU: read at fixed interval ----
  if (imu_initialized && (currentMillis - lastMPURead >= MPU_READ_INTERVAL)) {
    lastMPURead = currentMillis;
    readMPU6050();
  }

  // ---- Ultrasonic (20Hz notify) ----
  if (currentMillis - previousMillis >= SEND_INTERVAL_MS) {
    previousMillis = currentMillis;
    int left = checkObstacle(0);
    int center = checkObstacle(1);
    int right = checkObstacle(2);
    String data = String(left) + "," + String(center) + "," + String(right);
    Bridge.notify("ultrasonic", data);
  }

  // ---- Servo Smoothing (NEW) ----
  if (abs(target_pan - current_pan) > 0.2) {
    current_pan += (target_pan - current_pan) * EASE_FACTOR;
  } else {
    current_pan = target_pan;
  }

  if (abs(target_tilt - current_tilt) > 0.2) {
    current_tilt += (target_tilt - current_tilt) * EASE_FACTOR;
  } else {
    current_tilt = target_tilt;
  }

  servo_pan.write((int)constrain(current_pan, PAN_MIN, PAN_MAX));
  servo_tilt.write((int)constrain(current_tilt, TILT_MIN, TILT_MAX));

  delay(15);
}

// ============================================================
// ULTRASONIC FUNCTIONS (UNCHANGED)
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
// MPU6050 FUNCTIONS (UNCHANGED)
// ============================================================
bool initMPU6050() {
  mpu.begin();
  delay(100);
  mpu.writeToRegister(MPU6050_PWR_MGMT_1, 0x00);
  delay(10);
  mpu.writeToRegister(MPU6050_ACCEL_CONFIG, 0x10);
  delay(10);
  mpu.writeToRegister(MPU6050_GYRO_CONFIG, 0x08);
  delay(10);
  imu_initialized = true;
  return true;
}

void readMPU6050() {
  uint8_t data[14];
  mpu.readFromRegister(MPU6050_ACCEL_XOUT_H, data, 14);

  int16_t ax = (int16_t)((data[0] << 8) | data[1]);
  int16_t ay = (int16_t)((data[2] << 8) | data[3]);
  int16_t az = (int16_t)((data[4] << 8) | data[5]);

  cached_ax = ax / ACCEL_SCALE;
  cached_ay = ay / ACCEL_SCALE;
  cached_az = az / ACCEL_SCALE;

  int16_t gx = (int16_t)((data[8] << 8) | data[9]);
  int16_t gy = (int16_t)((data[10] << 8) | data[11]);
  int16_t gz = (int16_t)((data[12] << 8) | data[13]);

  cached_gx = gx / GYRO_SCALE;
  cached_gy = gy / GYRO_SCALE;
  cached_gz = gz / GYRO_SCALE;
}

String getAccelerometer() {
  if (!imu_initialized) return "0.00,0.00,0.00";
  char buffer[64];
  snprintf(buffer, sizeof(buffer), "%.2f,%.2f,%.2f", cached_ax, cached_ay, cached_az);
  return String(buffer);
}

float getIMUHeading() {
  if (!imu_initialized) return 0.0;
  static float yaw = 0.0;
  static unsigned long lastTime = micros();
  unsigned long currentTime = micros();
  float dt = (currentTime - lastTime) / 1000000.0;
  lastTime = currentTime;
  float gyroZ = cached_gz;
  yaw += gyroZ * dt;
  if (yaw < 0) yaw += 360;
  if (yaw >= 360) yaw -= 360;
  return yaw;
}

// ============================================================
// SERVO COMMAND CALLBACK (NEW)
// ============================================================
void onServoCommand(String data) {
  int comma = data.indexOf(',');
  if (comma > 0) {
    int pan = data.substring(0, comma).toInt();
    int tilt = data.substring(comma + 1).toInt();
    target_pan = constrain(pan, PAN_MIN, PAN_MAX);
    target_tilt = constrain(tilt, TILT_MIN, TILT_MAX);
  }
}

// ============================================================
// BUTTON CALLBACKS (UNCHANGED)
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
// SYSTEM CALLBACKS (UNCHANGED)
// ============================================================
int pingHandler() {
  return 1;
}