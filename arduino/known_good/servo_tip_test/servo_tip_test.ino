/*
  REPLAST - Servo tipping test

  Servo signal -> D6
  Servo power  -> separate 5V supply (NOT the Arduino 5V pin)
  Servo GND    -> supply GND AND Arduino GND (must be shared)

  Serial Monitor: 9600 baud. Type a letter and press Enter:
      l  -> tip LEFT (35), wait, come back level
      r  -> tip RIGHT (40), wait, come back level
      0  -> go LEVEL
*/

#include <Servo.h>

Servo tipper;

const uint8_t PIN_SERVO = 6;

const int LEVEL      = 90;   // flat position - adjust if not flat when mounted
const int TILT_LEFT  = 35;   // degrees to tip left
const int TILT_RIGHT = 40;   // degrees to tip right

const int SPEED_MS   = 15;   // ms per degree - bigger = slower, gentler tip
const int HOLD_MS    = 1000; // how long it stays tipped so the object falls

int angle = LEVEL;           // where the servo is now

// Move slowly, 1 degree at a time, so the object slides off instead of flying
void moveServo(int target) {
  while (angle != target) {
    angle += (target > angle) ? 1 : -1;
    tipper.write(angle);
    delay(SPEED_MS);
  }
}

void tip(int target) {
  moveServo(target);     // tip
  delay(HOLD_MS);        // let the object drop
  moveServo(LEVEL);      // back to flat
}

void setup() {
  Serial.begin(9600);
  tipper.attach(PIN_SERVO);
  tipper.write(LEVEL);   // start flat
  delay(500);

  Serial.println(F("REPLAST servo ready (level)."));
  Serial.println(F("l = tip left | r = tip right | 0 = level"));
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();

  if (c == 'l' || c == 'L') { Serial.println(F("Tip LEFT"));  tip(LEVEL - TILT_LEFT);  Serial.println(F("Level")); }
  else if (c == 'r' || c == 'R') { Serial.println(F("Tip RIGHT")); tip(LEVEL + TILT_RIGHT); Serial.println(F("Level")); }
  else if (c == '0')             { moveServo(LEVEL); Serial.println(F("Level")); }
}
