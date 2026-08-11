#include <Arduino_RouterBridge.h>
#include <OneButton.h>
#include <Wire.h>
#include <Servo.h>

// ============================================================
// SERVO CONFIGURATION
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

int last_written_pan = -1;
int last_written_tilt = -1;

// time-based ease (per-second rate), replaces per-loop-iteration factor
const float EASE_RATE = 8.0; // deg/sec convergence speed, tune as needed
unsigned long lastServoUpdate = 0;

void onServoCommand(String data);

// ============================================================
// I2C CONFIGURATION (hardware Wire2, A4/A5 on Uno Q)
// NOTE: confirmed via scanner — on this Uno Q unit, A4/A5 map to Wire2.
// ============================================================
#define MPU6050_ADDR 0x68

// ============================================================
// MPU6050 REGISTERS
// ============================================================
#define MPU6050_ACCEL_XOUT_H 0x3B
#define MPU6050_GYRO_XOUT_H  0x43
#define MPU6050_PWR_MGMT_1   0x6B
#define MPU6050_ACCEL_CONFIG 0x1C
#define MPU6050_GYRO_CONFIG  0x1B

// ============================================================
// SCALING FACTORS
// ============================================================
#define ACCEL_SCALE (4096.0 / 9.80665)
#define GYRO_SCALE 65.5

// ============================================================
// ULTRASONIC SENSORS – INTERRUPT DRIVEN, NON-BLOCKING
// ============================================================
const int trigPins[3] = {9, 7, 5};
const int echoPins[3] = {2, 8, 6};

const float ALPHA = 0.6;
float filtered[3] = {-1.0, -1.0, -1.0};
const float THRESHOLD_CM = 50.0;

const unsigned long US_TIMEOUT_US   = 30000; // 30ms max wait (~5m range)
const unsigned long TRIGGER_HIGH_US = 10;    // trigger pulse width
const unsigned long COOLDOWN_US     = 3000;  // gap between sensors, kill crosstalk

enum UltrasonicState {
  US_TRIG_HIGH,
  US_TRIG_LOW,
  US_WAIT_ECHO,
  US_COOLDOWN
};

UltrasonicState usState = US_TRIG_HIGH;
unsigned long usStateStart = 0;
int currentSensor = 0;

// ---- interrupt shared data ----
volatile unsigned long echoStart[3]    = {0, 0, 0};
volatile unsigned long echoDuration[3] = {0, 0, 0};
volatile bool echoReady[3]             = {false, false, false};

unsigned long previousNotifyMillis = 0;
const unsigned long NOTIFY_INTERVAL_MS = 50;

// ============================================================
// BUTTONS
// ============================================================
OneButton button(3, true);
OneButton sos_but(4, true);

// ============================================================
// MPU CACHE
// ============================================================
float cached_ax, cached_ay, cached_az;
float cached_gx, cached_gy, cached_gz;
bool imu_initialized = false;

unsigned long lastMPURead = 0;
const unsigned long MPU_READ_INTERVAL = 100;

// ============================================================
// FUNCTION PROTOTYPES
// ============================================================
int checkObstacle(int sensor_index);
int pingHandler();
float getIMUHeading();
String getAccelerometer();
bool initMPU6050();
void readMPU6050();
void initUltrasonic();
void processUltrasonic();
void sendUltrasonicData();
void echoISR0();
void echoISR1();
void echoISR2();

// ============================================================
// SETUP
// ============================================================
void setup() {
  Bridge.begin();
  Serial.begin(9600);

  // ---- Ultrasonic ----
  initUltrasonic();

  // ---- Buttons ----
  button.attachClick(shortClick);
  button.attachLongPressStop(longClick);
  sos_but.attachClick(shortClick_sos);
  sos_but.attachLongPressStop(longClick_sos);
  pinMode(4, INPUT_PULLUP);

  // ---- MPU6050 ----
  if (initMPU6050()) {
    Serial.println("MPU6050 ready (hardware I2C).");
    readMPU6050();
  } else {
    Serial.println("MPU6050 not found!");
  }

  // ---- Servos ----
  servo_pan.attach(PAN_PIN);
  servo_tilt.attach(TILT_PIN);
  servo_pan.write(90);
  servo_tilt.write(90);
  current_pan = 90;
  current_tilt = 90;
  target_pan = 90;
  target_tilt = 90;
  last_written_pan = 90;
  last_written_tilt = 90;

  // ---- RPC Callbacks ----
  Bridge.provide("ping", pingHandler);
  Bridge.provide("get_heading", getIMUHeading);
  Bridge.provide("get_accel", getAccelerometer);
  Bridge.provide("servo", onServoCommand);
  Serial.println("RPC handlers registered.");

  Serial.println("MCU ready: Interrupt ultrasonic + IMU + Servos + Buttons.");
}

// ============================================================
// LOOP
// ============================================================
void loop() {
  Bridge.update();
  button.tick();
  sos_but.tick();

  unsigned long currentMillis = millis();

  // ---- MPU ----
  if (imu_initialized && (currentMillis - lastMPURead >= MPU_READ_INTERVAL)) {
    lastMPURead = currentMillis;
    readMPU6050();
  }

  // ---- Ultrasonic ----
  processUltrasonic();

  // ---- Send data at 20Hz ----
  if (currentMillis - previousNotifyMillis >= NOTIFY_INTERVAL_MS) {
    previousNotifyMillis = currentMillis;
    sendUltrasonicData();
  }

  // ---- Servo smoothing (time-based, immune to loop-rate jitter) ----
  unsigned long nowMs = millis();
  float dt = (lastServoUpdate == 0) ? 0.0 : (nowMs - lastServoUpdate) / 1000.0;
  lastServoUpdate = nowMs;
  if (dt > 0.1) dt = 0.1; // clamp huge gaps (e.g. after blocking op)

  float step = EASE_RATE * dt;

  if (abs(target_pan - current_pan) > 0.5) {
    float diff = target_pan - current_pan;
    current_pan += constrain(diff, -step, step);
  } else {
    current_pan = target_pan;
  }

  if (abs(target_tilt - current_tilt) > 0.5) {
    float diff = target_tilt - current_tilt;
    current_tilt += constrain(diff, -step, step);
  } else {
    current_tilt = target_tilt;
  }

  int target_pan_int = (int)constrain(round(current_pan), PAN_MIN, PAN_MAX);
  int target_tilt_int = (int)constrain(round(current_tilt), TILT_MIN, TILT_MAX);

  if (target_pan_int != last_written_pan) {
    servo_pan.write(target_pan_int);
    last_written_pan = target_pan_int;
  }

  if (target_tilt_int != last_written_tilt) {
    servo_tilt.write(target_tilt_int);
    last_written_tilt = target_tilt_int;
  }
}

// ============================================================
// ULTRASONIC – INTERRUPT DRIVEN, NON-BLOCKING
// ============================================================
void echoISR(int idx) {
  if (digitalRead(echoPins[idx]) == HIGH) {
    echoStart[idx] = micros();
  } else {
    if (echoStart[idx] != 0) {
      echoDuration[idx] = micros() - echoStart[idx];
      echoReady[idx] = true;
      echoStart[idx] = 0;
    }
  }
}
void echoISR0() { echoISR(0); }
void echoISR1() { echoISR(1); }
void echoISR2() { echoISR(2); }

void initUltrasonic() {
  for (int i = 0; i < 3; i++) {
    pinMode(trigPins[i], OUTPUT);
    pinMode(echoPins[i], INPUT);
    digitalWrite(trigPins[i], LOW);
  }
  attachInterrupt(digitalPinToInterrupt(echoPins[0]), echoISR0, CHANGE);
  attachInterrupt(digitalPinToInterrupt(echoPins[1]), echoISR1, CHANGE);
  attachInterrupt(digitalPinToInterrupt(echoPins[2]), echoISR2, CHANGE);

  currentSensor = 0;
  usState = US_TRIG_HIGH;
  usStateStart = micros();
}

void processUltrasonic() {
  unsigned long now = micros();

  switch (usState) {

    case US_TRIG_HIGH:
      digitalWrite(trigPins[currentSensor], HIGH);
      usStateStart = now;
      usState = US_TRIG_LOW;
      break;

    case US_TRIG_LOW:
      if (now - usStateStart >= TRIGGER_HIGH_US) {
        digitalWrite(trigPins[currentSensor], LOW);
        echoReady[currentSensor] = false;
        usStateStart = now;
        usState = US_WAIT_ECHO;
      }
      break;

    case US_WAIT_ECHO:
      if (echoReady[currentSensor]) {
        float dist = echoDuration[currentSensor] * 0.034 / 2.0;
        if (filtered[currentSensor] < 0) {
          filtered[currentSensor] = dist;
        } else {
          filtered[currentSensor] = (ALPHA * dist) + ((1 - ALPHA) * filtered[currentSensor]);
        }
        echoReady[currentSensor] = false;
        usStateStart = now;
        usState = US_COOLDOWN;
      } else if (now - usStateStart > US_TIMEOUT_US) {
        filtered[currentSensor] = -1.0; // no object / timeout
        usStateStart = now;
        usState = US_COOLDOWN;
      }
      break;

    case US_COOLDOWN:
      if (now - usStateStart >= COOLDOWN_US) {
        currentSensor = (currentSensor + 1) % 3;
        usState = US_TRIG_HIGH;
      }
      break;
  }
}

void sendUltrasonicData() {
  int left = checkObstacle(0);
  int center = checkObstacle(1);
  int right = checkObstacle(2);
  String data = String(left) + "," + String(center) + "," + String(right);
  Bridge.notify("ultrasonic", data);
}

int checkObstacle(int sensor_index) {
  float dist = filtered[sensor_index];
  return (dist > 0 && dist < THRESHOLD_CM) ? 1 : 0;
}

// ============================================================
// MPU6050 FUNCTIONS (hardware I2C via Wire)
// ============================================================
void mpuWriteReg(uint8_t reg, uint8_t val) {
  Wire2.beginTransmission(MPU6050_ADDR);
  Wire2.write(reg);
  Wire2.write(val);
  Wire2.endTransmission();
}

bool initMPU6050() {
  Wire2.begin();
  Wire2.setClock(100000); // 100kHz first — raise to 400k only once stable
  delay(100);

  mpuWriteReg(MPU6050_PWR_MGMT_1, 0x00);
  delay(10);
  mpuWriteReg(MPU6050_ACCEL_CONFIG, 0x10);
  delay(10);
  mpuWriteReg(MPU6050_GYRO_CONFIG, 0x08);
  delay(10);

  // sanity check WHO_AM_I (should read 0x68)
  Wire2.beginTransmission(MPU6050_ADDR);
  Wire2.write(0x75);
  uint8_t err = Wire2.endTransmission(); // plain STOP, no repeated start
  if (err != 0) {
    Serial.print("MPU WHO_AM_I write fail, err=");
    Serial.println(err); // 1=data too long 2=addr NACK 3=data NACK 4=other/bus
    imu_initialized = false;
    return false;
  }
  Wire2.requestFrom((int)MPU6050_ADDR, 1);
  if (Wire2.available() < 1) {
    Serial.println("MPU WHO_AM_I no response.");
    imu_initialized = false;
    return false;
  }
  uint8_t whoami = Wire2.read();
  Serial.print("MPU WHO_AM_I = 0x");
  Serial.println(whoami, HEX); // expect 0x68

  imu_initialized = true;
  return true;
}

unsigned long mpuFailCount = 0;

void readMPU6050() {
  uint8_t data[14];

  Wire2.beginTransmission(MPU6050_ADDR);
  Wire2.write(MPU6050_ACCEL_XOUT_H);
  uint8_t err = Wire2.endTransmission(); // plain STOP, no repeated start
  if (err != 0) {
    mpuFailCount++;
    if (mpuFailCount % 20 == 1) { // throttle spam
      Serial.print("MPU read fail (write), err=");
      Serial.println(err);
    }
    return;
  }

  uint8_t n = Wire2.requestFrom((int)MPU6050_ADDR, 14);
  if (n < 14) {
    mpuFailCount++;
    if (mpuFailCount % 20 == 1) {
      Serial.print("MPU read fail (short), got=");
      Serial.println(n);
    }
    return;
  }

  for (int i = 0; i < 14; i++) {
    data[i] = Wire2.read();
  }

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
  snprintf(buffer, sizeof(buffer), "%.2f,%.2f,%.2f",
           cached_ax, cached_ay, cached_az);
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
// SERVO COMMAND CALLBACK
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
int pingHandler() {
  return 1;
}