#include <Arduino_RouterBridge.h>

const int trigPins[3] = {9, 7, 5};
const int echoPins[3] = {10, 8, 6};
long duration;

// Exponential moving average variables
const float ALPHA = 0.6;
float filtered[3] = {-1.0, -1.0, -1.0};

// Threshold for obstacle detection (100cm)
const float THRESHOLD_CM = 50.0;

// Track previous state to avoid sending duplicate messages
int previous_state[3] = {0, 0, 0};  // 0 = clear, 1 = obstacle

void setup() {
  Bridge.begin();
  Serial.begin(9600);  // For debugging if needed
  
  for (int i = 0; i < 3; i++) {
    pinMode(trigPins[i], OUTPUT);
    pinMode(echoPins[i], INPUT);
    digitalWrite(trigPins[i], LOW);
  }
  
  // Register RPC callbacks
  Bridge.provide("read_left",   getLeftStatus);
  Bridge.provide("read_center", getCenterStatus);
  Bridge.provide("read_right",  getRightStatus);
  Bridge.provide("ping", pingHandler);
}

float getDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  duration = pulseIn(echo, HIGH);
  
  float raw_dist = (duration == 0) ? -1.0 : (duration * 0.034 / 2);
  
  // Apply exponential moving average
  int idx = (echo == echoPins[0]) ? 0 : (echo == echoPins[1]) ? 1 : 2;
  
  if (raw_dist > 0) {
    if (filtered[idx] < 0) {
      filtered[idx] = raw_dist;
    } else {
      filtered[idx] = (ALPHA * raw_dist) + ((1 - ALPHA) * filtered[idx]);
    }
    return filtered[idx];
  } else {
    filtered[idx] = -1.0;
    return -1.0;
  }
}

// Returns: 1 = obstacle detected, 0 = clear
int checkObstacle(int sensor_index) {
  float dist = getDistance(trigPins[sensor_index], echoPins[sensor_index]);
  
  // Check if distance is valid and within threshold
  if (dist > 0 && dist < THRESHOLD_CM) {
    return 1;  // Obstacle detected
  } else {
    return 0;  // Clear path
  }
}

void loop() {
  Bridge.update();
  
  // Optional: Print debug info to Serial
  // Serial.print("L:");
  // Serial.print(checkObstacle(0));
  // Serial.print(" C:");
  // Serial.print(checkObstacle(1));
  // Serial.print(" R:");
  // Serial.println(checkObstacle(2));
  
  delay(10);  // Small delay to prevent overwhelming the bridge
}

// RPC callback functions - return 1 for obstacle, 0 for clear
int getLeftStatus() { 
  return checkObstacle(0); 
}

int getCenterStatus() { 
  return checkObstacle(1); 
}

int getRightStatus() { 
  return checkObstacle(2); 
}

int pingHandler() { 
  return 1; 
}
