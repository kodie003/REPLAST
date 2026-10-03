/*
  REPLAST - NEMA 17 platform control (no libraries)

  Pins:  STEP -> D9   DIR -> D10   ENABLE -> D11
         RST and SLP joined together on the A4988

  Serial Monitor: 9600 baud. Type a letter and press Enter:
      0  -> go HOME (centre)
      r  -> go 90 degrees RIGHT
      l  -> go 90 degrees LEFT

  Rule: when switching sides (right -> left or left -> right),
  the platform always stops at HOME first, then turns 90.

  HOME = wherever the platform is when the Arduino starts.
  Line it up by hand before powering on.
*/

const uint8_t PIN_STEP   = 9;
const uint8_t PIN_DIR    = 10;
const uint8_t PIN_ENABLE = 11;   // LOW = driver on

const int MICROSTEPS = 1;        // full-step mode (MS pins not connected)

const long STEPS_90 = 200L * MICROSTEPS / 4;   // 90 degrees = 50 steps

// speed (time per step in microseconds) - bigger = slower
const unsigned int START_US = (MICROSTEPS == 16) ? 2000 : 20000;  // slow start
const unsigned int FAST_US  = (MICROSTEPS == 16) ? 800  : 8000;   // top speed

const unsigned int HOME_PAUSE_MS = 100; // pause at home when switching sides

long position = 0;   // current position in steps (0 = home)

void pulse(unsigned int waitUs) {
  digitalWrite(PIN_STEP, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_STEP, LOW);
  if (waitUs > 10000) delay(waitUs / 1000);
  else delayMicroseconds(waitUs);
}

// Move straight to a position, speeding up at the start and slowing down at the end
void moveTo(long target) {
  long steps = abs(target - position);
  if (steps == 0) return;

  digitalWrite(PIN_DIR, (target > position) ? HIGH : LOW);
  delayMicroseconds(20);

  long ramp = steps / 4;
  if (ramp < 1) ramp = 1;

  for (long i = 0; i < steps; i++) {
    long fromEdge = min(i, steps - 1 - i);
    unsigned int w = FAST_US;
    if (fromEdge < ramp) {
      w = START_US - (long)(START_US - FAST_US) * fromEdge / ramp;
    }
    pulse(w);
  }
  position = target;
}

// Go to a side. If we are on the OTHER side, stop at HOME first.
void goToSide(long target) {
  bool onOtherSide = (position > 0 && target < 0) || (position < 0 && target > 0);

  if (onOtherSide) {
    moveTo(0);                       // step 1: back to home
    position = 0;                    // reset home to 0
    Serial.println(F("  at HOME"));
    delay(HOME_PAUSE_MS);
  }
  moveTo(target);                    // step 2: just 90 degrees
}

void setup() {
  Serial.begin(9600);
  pinMode(PIN_STEP, OUTPUT);
  pinMode(PIN_DIR, OUTPUT);
  pinMode(PIN_ENABLE, OUTPUT);
  digitalWrite(PIN_ENABLE, LOW);   // driver on, holds the platform still

  Serial.println(F("REPLAST platform ready. Current spot = HOME."));
  Serial.println(F("0 = home | r = right 90 | l = left 90"));
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();

  if (c == '0')                  { moveTo(0);              Serial.println(F("HOME")); }
  else if (c == 'r' || c == 'R') { goToSide(STEPS_90);     Serial.println(F("RIGHT 90")); }
  else if (c == 'l' || c == 'L') { goToSide(-STEPS_90);    Serial.println(F("LEFT 90")); }
}
