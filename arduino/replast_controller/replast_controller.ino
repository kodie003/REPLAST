/*
  REPLAST - main controller (Arduino UNO R4 WiFi)

  The four jobs this sketch does (search for the function names):
    1. ROTATE the NEMA 17 to the right compartment ..... startRotation(), updateRotation()
    2. TIP the servo to drop the item in ................ startTip(), updateServo()
    3. DETECT an item with the IR sensor + BEEP ......... checkForItem(), beep()
    4. SHOW what is happening on the OLED ............... showScreen(), updateScreenTimers()

  What a normal cycle looks like
  ------------------------------
    IR sees an item  -> beep, screen "ITEM IN", Pi is told "OBJECT"
    (0.7 s later)    -> screen "SCANNING" while the Pi's camera classifies it
    Pi sends e.g. SORT_PAPER -> screen "PAPER  -> bin 2", ACK:SORT_PAPER
    rotate to bin 2  -> screen "DROPPING", tip right, hold, level
    rotate home      -> screen "SORTED", DONE:SORT_PAPER
    (2.5 s later)    -> screen "READY"

  Safety
  ------
  * No long delay(): motors move a little on every pass of loop(), so a
    STOP from the Pi is acted on within milliseconds.
  * STOP halts both motors where they are and refuses any movement until
    RESET. The driver stays enabled so the step count stays correct.
  * Every movement has a time limit. Exceeding it = halt + ERR:TIMEOUT.
  * The OLED is only redrawn while the stepper is NOT turning (a redraw
    takes several ms and would make the motor stutter). If the OLED is missing
    or broken, the machine still sorts.

  Wiring (docs/HARDWARE.md)
    STEP D9, DIR D10 (HIGH = clockwise), ENABLE D11 (LOW = on), A4988 (MICROSTEPS below)
    MG996R servo D6 (6 V from the buck converter, shared GND)
    Sharp IR sensor A2, passive buzzer D5, SSD1306 OLED on A4 (SDA) / A5 (SCL), address 0x3C

  Works on BOTH the classic Arduino Uno (R3) and the UNO R4 WiFi: pick the
  board you actually have in Tools -> Board.

  Libraries (Arduino IDE -> Tools -> Manage Libraries):
    "U8g2" by oliver (text-only U8x8 mode: needs no screen memory, which the
    classic Uno's 2 KB RAM could not spare). Servo and Wire come with the IDE.

  HOME = where the platform is at power-on. Line it up over the PET
  compartment (compartment 1) by hand BEFORE switching on.

  Serial: 9600 baud, one message per line (docs/PROTOCOL.md). You can also
  type the commands in the Serial Monitor (line ending "Newline").
*/

#include <Servo.h>
#include <Wire.h>
#include <U8x8lib.h>

// ===================== STEPPER RESOLUTION ================================
// How many pulses the A4988 needs per full motor step. MUST match the
// MS1/MS2/MS3 wiring:
//   all three unconnected / LOW .... 1  (full step: 200 pulses per turn)
//   MS1 HIGH ....................... 2
//   MS2 HIGH ....................... 4
//   MS1 + MS2 HIGH ................. 8
//   MS1 + MS2 + MS3 HIGH ........... 16
// 1 is what the machine does today (3 Oct 2026: 800 pulses turned the
// platform about 4 full turns, i.e. the driver is at full step), and
// what the known-good stepper sketch used. Check with "JOG:50" (should
// be exactly 90 degrees clockwise at MICROSTEPS 1).
const long MICROSTEPS = 1;
const long STEPS_PER_BIN = 50 * MICROSTEPS;   // 90 degrees = 50 full steps

// ===================== BIN MAP (the only place routes live) ==============
// Positions are in pulses from HOME (= PET, compartment 1).
// Positive = clockwise (DIR HIGH), negative = counter-clockwise.
// After every drop the platform returns HOME, so going e.g. from the
// aluminium bin to the paper bin is: 90 CW back home, then 90 CW to paper.
struct Route {
  const char *command;
  long target;          // where to rotate to before tipping
  const char *title;    // big word on the screen
  const char *detail;   // second line on the screen
};

const Route ROUTES[] = {
  // title: max 8 characters, detail: max 16 characters (screen width)
  { "SORT_PET",   0,                  "PLASTIC", "PET -> bin 1"    },  // home: just tip
  { "SORT_PAPER", +1 * STEPS_PER_BIN, "PAPER",   "Paper -> bin 2"  },  //  90 deg clockwise
  { "SORT_AL",    -1 * STEPS_PER_BIN, "METAL",   "Alumin. -> bin 3" },  //  90 deg counter-clockwise
  { "REJECT",     -2 * STEPS_PER_BIN, "REJECT",  "Unsure -> bin 4" },  // 180 deg counter-clockwise
};
const int NUM_ROUTES = sizeof(ROUTES) / sizeof(ROUTES[0]);
// The servo always tips RIGHT (SERVO_TIP below), on every route.

// ===================== PINS ==============================================
const uint8_t PIN_STEP   = 9;
const uint8_t PIN_DIR    = 10;
const uint8_t PIN_ENABLE = 11;
const uint8_t PIN_SERVO  = 6;
const uint8_t PIN_BUZZER = 5;
const int     PIN_IR     = A2;

// ===================== STEPPER ===========================================
// Speeds in FULL steps, taken from the known-good sketch (20 ms/step at
// the start and end of a move, 8 ms/step at full speed). Scaled by
// MICROSTEPS below, so changing MICROSTEPS keeps the same real speed.
const float START_SPEED = 50.0  * MICROSTEPS;   // pulses/s at the very start and end
const float MAX_SPEED   = 125.0 * MICROSTEPS;   // pulses/s cruising
const float ACCEL       = 500.0 * MICROSTEPS;   // pulses/s^2
const unsigned long SETTLE_MS = 500;         // pause after turning, before tipping (asked for 3 Oct 2026)
const unsigned long MOVE_TIMEOUT_MS = 6000;  // 180 deg takes ~1 s; 6 s means something is wrong
const long MAX_JOG = 4 * STEPS_PER_BIN;      // JOG limit: one full turn

// ===================== SERVO (same numbers as the known-good sketch) =====
const int SERVO_LEVEL = 90;
const int SERVO_TIP   = 90 + 40;          // tip RIGHT by 40 degrees
const unsigned long SERVO_STEP_MS = 15;   // ms per degree: slow, so items slide off
const unsigned long TIP_HOLD_MS   = 1000; // stay tipped while the item drops
const unsigned long SERVO_TIMEOUT_MS = 3000;

// ===================== IR SENSOR + BUZZER ================================
// !!! PLACEHOLDERS - replace with the RESULT lines from ir_check.ino !!!
const bool ITEM_READS_HIGHER = true;   // RESULT direction=ITEM_HIGHER -> true
const int  IR_THRESHOLD      = 300;    // RESULT threshold=...
const int  IR_HYSTERESIS     = 20;     // RESULT hysteresis=...
const unsigned long IR_OBJECT_MS = 500; // item must be seen steadily this long
const unsigned long IR_CLEAR_MS  = 300; // platform must look empty this long
const unsigned long IR_SAMPLE_MS = 20;  // read the sensor 50 times a second

const unsigned int  BEEP_HZ = 2000;
const unsigned long BEEP_MS = 150;

// ===================== OLED ==============================================
const uint8_t OLED_ADDRESS = 0x3C;
const unsigned long SCAN_AFTER_MS  = 700;   // "ITEM IN" -> "SCANNING"
const unsigned long SORTED_SHOW_MS = 2500;  // "SORTED" -> "READY"

// Text-only driver: 16 columns x 8 rows of 8x8-pixel characters.
U8X8_SSD1306_128X64_NONAME_HW_I2C oled(U8X8_PIN_NONE);
bool oledOk = false;

enum Screen { SCR_READY, SCR_ITEM, SCR_SCANNING, SCR_SORTING, SCR_DROPPING, SCR_SORTED, SCR_STOPPED };
Screen screen = SCR_READY;
unsigned long screenSince = 0;

// ===================== STATE =============================================
enum Phase {
  IDLE,         // waiting; watching the IR sensor
  MOVE_OUT,     // rotating to the compartment
  SETTLE,       // short pause so the platform stops wobbling
  TIP_DOWN,     // servo tipping right
  TIP_HOLD,     // holding tipped while the item drops
  TIP_UP,       // servo returning level
  MOVE_HOME,    // rotating back to step 0
  STOPPED,      // after STOP or an error: nothing moves until RESET
  JOGGING       // JOG command: turning by hand-test amount, no tipping
};
const char *PHASE_NAMES[] = {
  "IDLE", "MOVE_OUT", "SETTLE", "TIP_DOWN", "TIP_HOLD", "TIP_UP", "MOVE_HOME", "STOPPED", "JOGGING"
};

Phase phase = IDLE;
char activeCmd[16] = "";       // command being carried out (for ACK/DONE)
const Route *activeRoute = NULL;

// stepper
long position = 0;             // current position in microsteps (0 = home)
long moveTarget = 0;
long moveTotal = 0, moveDone = 0;
int  moveDir = 1;
unsigned long nextStepUs = 0;
unsigned long phaseStartMs = 0;

// servo
Servo tipper;
int servoAngle = SERVO_LEVEL;
int servoTarget = SERVO_LEVEL;
unsigned long lastServoMs = 0;

// IR
bool irItem = false;           // debounced: is an item there?
int  irLast = 0;
unsigned long irChangeSince = 0;
unsigned long lastIrMs = 0;

// serial input
char rxBuf[33];
uint8_t rxLen = 0;
bool rxOverflow = false;

// ===================== SERIAL REPLIES ====================================

// Two functions rather than one with a default argument: the Arduino IDE
// writes its own copies of function headers, and default arguments break that.
void reply(const char *msg) {
  Serial.println(msg);
}

void reply2(const char *a, const char *b) {
  Serial.print(a);
  Serial.println(b);
}

void enterPhase(Phase p) {
  phase = p;
  phaseStartMs = millis();
}

// ===================== 4. DISPLAY ========================================

// Draw one screen: a big word on top (max 8 characters) and two small
// lines under it (max 16 characters each).
// Only call this when the stepper is not turning (see "Safety" above).
void showScreen(Screen s, const char *big, const char *line2, const char *line3) {
  screen = s;
  screenSince = millis();
  if (!oledOk) return;
  oled.clear();
  oled.draw2x2String(0, 0, big);  // double size: rows 0-1
  oled.drawString(0, 4, line2);
  oled.drawString(0, 6, line3);
}

void showReady()    { showScreen(SCR_READY, "READY", "Place ONE item", "on the platform"); }
void showItemIn()   { showScreen(SCR_ITEM, "ITEM IN", "Item detected", "Please wait..."); }
void showScanning() { showScreen(SCR_SCANNING, "SCANNING", "Camera checking", "the material..."); }

// Timed screen changes. Only happen while IDLE, so the stepper is still.
void updateScreenTimers() {
  if (phase != IDLE) return;
  unsigned long age = millis() - screenSince;
  if (screen == SCR_ITEM && age >= SCAN_AFTER_MS) showScanning();
  else if (screen == SCR_SORTED && age >= SORTED_SHOW_MS) showReady();
}

// ===================== 3. IR SENSOR + BUZZER =============================

void beep() {
  tone(PIN_BUZZER, BEEP_HZ, BEEP_MS);   // runs in the background, does not block
}

int readIR() {
  long sum = 0;
  for (int k = 0; k < 4; k++) sum += analogRead(PIN_IR);   // Sharp sensor is noisy: average 4
  return (int)(sum / 4);
}

// Debounced detection: an item must be seen for IR_OBJECT_MS before it
// counts, and the platform must look empty for IR_CLEAR_MS before it counts
// as clear. Hysteresis stops flicker when a reading sits on the threshold.
void checkForItem() {
  if (millis() - lastIrMs < IR_SAMPLE_MS) return;
  lastIrMs = millis();
  irLast = readIR();

  // While the platform moves the sensor sees odd things; ignore it.
  if (phase != IDLE) {
    irChangeSince = millis();
    return;
  }

  bool looksLikeItem, looksEmpty;
  if (ITEM_READS_HIGHER) {
    looksLikeItem = irLast > IR_THRESHOLD + IR_HYSTERESIS / 2;
    looksEmpty    = irLast < IR_THRESHOLD - IR_HYSTERESIS / 2;
  } else {
    looksLikeItem = irLast < IR_THRESHOLD - IR_HYSTERESIS / 2;
    looksEmpty    = irLast > IR_THRESHOLD + IR_HYSTERESIS / 2;
  }

  bool wantsChange = irItem ? looksEmpty : looksLikeItem;
  if (!wantsChange) {
    irChangeSince = millis();
    return;
  }
  unsigned long needed = irItem ? IR_CLEAR_MS : IR_OBJECT_MS;
  if (millis() - irChangeSince < needed) return;

  irItem = !irItem;
  if (irItem) {
    reply("OBJECT");
    beep();
    showItemIn();
  } else {
    reply("CLEAR");
    // Item taken away before it was sorted: go back to READY.
    if (screen == SCR_ITEM || screen == SCR_SCANNING) showReady();
  }
}

// ===================== 1. ROTATION (NEMA 17) =============================

// Time between two pulses for pulse number i of a move. Starts at
// START_SPEED, speeds up at ACCEL until MAX_SPEED, cruises, then slows
// down the same way, so the platform starts and stops gently
// (v = sqrt(v0^2 + 2 * a * distance)).
unsigned long stepPeriodUs(long i, long total) {
  long fromEdge = min(i, total - 1 - i);     // pulses from the nearer end
  float v = sqrt(START_SPEED * START_SPEED + 2.0 * ACCEL * fromEdge);
  if (v > MAX_SPEED) v = MAX_SPEED;
  return (unsigned long)(1000000.0 / v);
}

void startRotation(long target) {
  moveTarget = target;
  moveTotal = labs(target - position);
  moveDone = 0;
  moveDir = (target > position) ? 1 : -1;
  digitalWrite(PIN_DIR, (moveDir > 0) ? HIGH : LOW);   // HIGH = clockwise
  delayMicroseconds(20);         // A4988 needs DIR settled before the first STEP
  nextStepUs = micros();
}

// Do at most one microstep if it is time. Returns true when the move is finished.
bool updateRotation() {
  if ((long)(micros() - nextStepUs) < 0) return false;   // not time yet
  if (moveDone >= moveTotal) return true;                 // last step's wait is over

  digitalWrite(PIN_STEP, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_STEP, LOW);

  position += moveDir;           // count every step, so STOP keeps the position right
  nextStepUs = micros() + stepPeriodUs(moveDone, moveTotal);
  moveDone++;
  return false;
}

// ===================== 2. TIPPING (SERVO) ================================

void startTip(int angle) {
  servoTarget = angle;
}

// Move the servo one degree towards its target if it is time.
// Returns true when it has arrived.
bool updateServo() {
  if (servoAngle == servoTarget) return true;
  if (millis() - lastServoMs < SERVO_STEP_MS) return false;
  lastServoMs = millis();
  servoAngle += (servoTarget > servoAngle) ? 1 : -1;
  tipper.write(servoAngle);
  return servoAngle == servoTarget;
}

// ===================== STOP / ERRORS =====================================

void haltMotors() {
  moveTotal = moveDone;          // abandon the stepper move (driver stays on and holds)
  servoTarget = servoAngle;      // freeze the servo where it is
}

void fail(const char *code) {
  haltMotors();
  enterPhase(STOPPED);
  reply2("ERR:", code);
  showScreen(SCR_STOPPED, "ERROR", code, "Needs RESET");
}

bool timedOut(unsigned long limitMs) {
  return millis() - phaseStartMs > limitMs;
}

// ===================== THE SORT SEQUENCE =================================

void finishCommand() {
  enterPhase(IDLE);
  reply2("DONE:", activeCmd);
  if (activeRoute != NULL) showScreen(SCR_SORTED, "SORTED", activeRoute->detail, "Thank you!");
  else                     showReady();      // after RESET
  activeCmd[0] = '\0';
  activeRoute = NULL;
  irChangeSince = millis();      // ignore IR readings taken while moving
}

void updateSequence() {
  switch (phase) {
    case IDLE:
    case STOPPED:
      break;

    case MOVE_OUT:
      if (updateRotation()) enterPhase(SETTLE);
      else if (timedOut(MOVE_TIMEOUT_MS)) fail("TIMEOUT");
      break;

    case SETTLE:
      if (millis() - phaseStartMs >= SETTLE_MS) {
        if (activeRoute != NULL) showScreen(SCR_DROPPING, "DROPPING", activeRoute->detail, "");
        startTip(SERVO_TIP);
        enterPhase(TIP_DOWN);
      }
      break;

    case TIP_DOWN:
      if (updateServo()) enterPhase(TIP_HOLD);
      else if (timedOut(SERVO_TIMEOUT_MS)) fail("TIMEOUT");
      break;

    case TIP_HOLD:
      if (millis() - phaseStartMs >= TIP_HOLD_MS) {
        startTip(SERVO_LEVEL);
        enterPhase(TIP_UP);
      }
      break;

    case TIP_UP:
      if (updateServo()) {
        startRotation(0);          // level first, THEN rotate home
        enterPhase(MOVE_HOME);
      } else if (timedOut(SERVO_TIMEOUT_MS)) fail("TIMEOUT");
      break;

    case MOVE_HOME:
      if (updateRotation()) finishCommand();
      else if (timedOut(MOVE_TIMEOUT_MS)) fail("TIMEOUT");
      break;

    case JOGGING:
      if (updateRotation()) {
        enterPhase(IDLE);
        reply2("DONE:", activeCmd);
        activeCmd[0] = '\0';
        Serial.print("# position now ");
        Serial.println(position);
      } else if (timedOut(MOVE_TIMEOUT_MS)) fail("TIMEOUT");
      break;
  }
}

// ===================== COMMANDS FROM THE PI ==============================

const Route *findRoute(const char *cmd) {
  for (int i = 0; i < NUM_ROUTES; i++) {
    if (strcmp(cmd, ROUTES[i].command) == 0) return &ROUTES[i];
  }
  return NULL;
}

void sendStatus() {
  Serial.print("STATUS:");
  Serial.print(PHASE_NAMES[phase]);
  Serial.print(",POS=");
  Serial.print(position);
  Serial.print(",SERVO=");
  Serial.print(servoAngle);
  Serial.print(",IR=");
  Serial.print(irLast);
  Serial.print(",ITEM=");
  Serial.println(irItem ? 1 : 0);
}

void handleCommand(char *cmd) {
  // Accept lower case typed in the Serial Monitor.
  for (char *p = cmd; *p; p++) {
    if (*p >= 'a' && *p <= 'z') *p = *p - 'a' + 'A';
  }

  if (strcmp(cmd, "PING") == 0)   { reply("PONG"); return; }
  if (strcmp(cmd, "STATUS") == 0) { sendStatus(); return; }

  if (strcmp(cmd, "STOP") == 0) {
    haltMotors();
    activeCmd[0] = '\0';
    activeRoute = NULL;
    enterPhase(STOPPED);
    reply("STOPPED");
    showScreen(SCR_STOPPED, "STOPPED", "Motors halted", "Needs RESET");
    return;
  }

  if (strcmp(cmd, "RESET") == 0) {
    if (phase != IDLE && phase != STOPPED) { reply("ERR:BUSY"); return; }
    strcpy(activeCmd, "RESET");
    activeRoute = NULL;
    reply2("ACK:", activeCmd);
    showScreen(SCR_SORTING, "RESET", "Going home...", "");
    // Level the servo first, then go home: reuse the end of the sort sequence.
    startTip(SERVO_LEVEL);
    enterPhase(TIP_UP);
    return;
  }

  // JOG:<n> - turn n pulses (+ clockwise, - counter-clockwise) and stop
  // there. For checking/adjusting the angle by hand from the Serial Monitor.
  if (strncmp(cmd, "JOG:", 4) == 0) {
    long n = atol(cmd + 4);
    if (phase == STOPPED)  { reply("ERR:STOPPED"); return; }
    if (phase != IDLE)     { reply("ERR:BUSY"); return; }
    if (n == 0 || labs(n) > MAX_JOG) { reply("ERR:BAD_JOG"); return; }
    strcpy(activeCmd, "JOG");
    activeRoute = NULL;
    reply2("ACK:", activeCmd);
    startRotation(position + n);
    enterPhase(JOGGING);
    return;
  }

  // HOME - "the platform is now exactly over PET": make this position 0.
  // Use after lining it up by hand or with JOG.
  if (strcmp(cmd, "HOME") == 0) {
    if (phase != IDLE && phase != STOPPED) { reply("ERR:BUSY"); return; }
    position = 0;
    reply("ACK:HOME");
    reply("DONE:HOME");
    return;
  }

  const Route *r = findRoute(cmd);
  if (r == NULL)        { reply("ERR:UNKNOWN_CMD"); return; }
  if (phase == STOPPED) { reply("ERR:STOPPED"); return; }
  if (phase != IDLE)    { reply("ERR:BUSY"); return; }

  strcpy(activeCmd, r->command);
  activeRoute = r;
  reply2("ACK:", activeCmd);
  showScreen(SCR_SORTING, r->title, r->detail, "Moving...");  // drawn BEFORE the motor starts
  startRotation(r->target);      // 0 steps for PET: MOVE_OUT finishes at once
  enterPhase(MOVE_OUT);
}

void readSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      rxBuf[rxLen] = '\0';
      if (rxOverflow)      reply("ERR:UNKNOWN_CMD");
      else if (rxLen > 0)  handleCommand(rxBuf);
      rxLen = 0;
      rxOverflow = false;
    } else if (rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = c;
    } else {
      rxOverflow = true;          // line too long: throw it away
    }
  }
}

// ===================== SETUP / LOOP ======================================

void setup() {
  pinMode(PIN_STEP, OUTPUT);
  pinMode(PIN_DIR, OUTPUT);
  pinMode(PIN_ENABLE, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_STEP, LOW);
  digitalWrite(PIN_ENABLE, LOW);   // driver on: holds the platform at HOME

  tipper.attach(PIN_SERVO);
  tipper.write(SERVO_LEVEL);

#if !defined(ARDUINO_ARCH_AVR)
  analogReadResolution(10);        // R4: force 0..1023 (a classic Uno is always 0..1023)
#endif
  pinMode(PIN_IR, INPUT);

  Serial.begin(9600);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 2000) { }

  // Is the OLED there? (The library would happily "draw" to nothing.)
  Wire.begin();
  Wire.beginTransmission(OLED_ADDRESS);
  oledOk = (Wire.endTransmission() == 0);
  if (oledOk) {
    oled.setI2CAddress(OLED_ADDRESS * 2);   // the library wants the 8-bit form (0x78)
    oled.setBusClock(400000);              // fast I2C so a redraw is short
    oledOk = oled.begin();
    oled.setFont(u8x8_font_chroma48medium8_r);
  }
  if (!oledOk) Serial.println("# OLED not found at 0x3C - running without display");
  showScreen(SCR_READY, "REPLAST", "Starting...", "");

  delay(500);                      // let the servo reach level
  irChangeSince = millis();
  Serial.println("# REPLAST controller v2");
  showReady();
  reply("READY");
}

void loop() {
  readSerial();          // first, so STOP is seen as early as possible
  updateSequence();      // jobs 1 and 2: rotate and tip
  checkForItem();        // job 3: IR + buzzer
  updateScreenTimers();  // job 4: timed screen changes
}
