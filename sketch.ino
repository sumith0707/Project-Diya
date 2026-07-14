#include <RPC.h> // Native Uno Q RPC/Bridge library

// Sensor Pins Array
const int trigPins[3] = {9, 7, 5};   // Left, Center, Right triggers
const int echoPins[3] = {10, 8, 6};  // Left, Center, Right echoes

float distances[3] = {0.0, 0.0, 0.0};
unsigned long previous_millis = 0;
const unsigned long interval = 60;   // Sensor cycle timing in milliseconds

void setup() {
  RPC.begin(); // Initialize the RPC communication link to the Linux OS
  
  // Configure all pins in a clean loop
  for(int i = 0; i < 3; i++) {
    pinMode(trigPins[i], OUTPUT);
    pinMode(echoPins[i], INPUT);
    digitalWrite(trigPins[i], LOW);
  }
}

// Microsecond-level hardware trigger function
float getDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  
  // 30ms timeout stops code from freezing if a sensor disconnects
  long duration = pulseIn(echo, HIGH, 30000); 
  if (duration == 0) return -1.0; // Return -1 if out of range or disconnected
  
  return (duration * 0.034) / 2;
}

void loop() {
  unsigned long current_millis = millis();

  // Non-blocking timer using millis()
  if (current_millis - previous_millis >= interval) {
    previous_millis = current_millis;

    // Measure each sensor sequentially
    for(int i = 0; i < 3; i++) {
      distances[i] = getDistance(trigPins[i], echoPins[i]);
    }

    // Pass the raw data directly to the Linux shared memory space
    RPC.set("left_sensor", distances[0]);
    RPC.set("center_sensor", distances[1]);
    RPC.set("right_sensor", distances[2]);
  }
}
