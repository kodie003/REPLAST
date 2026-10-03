/*
  REPLAST - main controller (Arduino UNO R4 WiFi)

  What this sketch does
  ---------------------
  * Watches the IR sensor and tells the Pi "OBJECT" when an item is sitting
    on the platform, and "CLEAR" when it has gone.
  * Waits for the Pi's decision (SORT_PET / SORT_PAPER / SORT_AL / REJECT),
    then: rotate to the compartment -> tip right -> level -> rotate home.
  * Answers every order with ACK:<cmd> straight away and DONE:<cmd> when the
    platform is back home. Full message list: docs/PROTOCOL.md.

  Safety
  ------
  * Nothing here ever blocks in a long delay(): motors are moved a little on
    every pass of loop(), so a STOP from the Pi is acted on within ~20 ms.
  * STOP halts both motors where they are and refuses any movement until a
    RESET. The driver stays enabled so the platform holds its position and
    the step count stays correct.
  * Every movement has a time limit. Exceeding it = halt + ERR:TIMEOUT.

  Built from the known-good test sketches in arduino/known_good/:
  same pins, same step timing and ramp, same servo speed and tip angle.

  Wiring (docs/HARDWARE.md)
    STEP D9, DIR D10 (HIGH = clockwise), ENABLE D11 (LOW = on)
    Servo D6 (own 5 V supply, shared GND)
    Sharp IR sensor A2

  HOME = where the platform is at power-on. Line it up over the PET
  compartment (compartment 1) by hand BEFORE switching on.

  Serial: 9600 baud, one message per line. You can also type the commands
  in the Arduino Serial Monitor (line ending "Newline") to test by hand.
*/

#include <Servo.h>

// ===================== BIN MAP (the only place routes live) ==============
// Positions are in full steps from HOME. 50 steps = 90 degrees.
// Positive = clockwise (DIR HIGH), negative = counter-clockwise.
// Compartments are numbered counter-clockwise from home.
struct Route {
  const char *command;
  long target;          // where to rotate to before tipping
};

const Route ROUTES[] = {
  { "SORT_PET",      0 },   // compartment 1: home, no rotation
  { "SORT_PAPER",  -50 },   // compartment 2:  90 deg counter-clockwise
  { "SORT_AL",    -100 },   // compartment 3: 180 deg counter-clockwise
  { "REJECT",      +50 },   // compartment 4:  90 deg clockwise
};
const int NUM_ROUTES = sizeof(ROUTES) / sizeof(ROUTES[0]);
// The servo always tips RIGHT (see SERVO_TIP below), on every route.

// ===================== PINS ==============================================
const uint8_t PIN_STEP   = 9;
const uint8_t PIN_DIR    = 10;
const uint8_t PIN_ENABLE = 11;
const uint8_t PIN_SERVO  = 6;
const int     PIN_IR     = A2;

// ===================== STEPPER (same numbers as the known-good sketch) ===
const unsigned long START_US = 20000;  // slowest step period (start/end of a move)
const unsigned long FAST_US  = 8000;   // fastest step period (middle of a move)
const unsigned long SETTLE_MS = 200;   // pause after arriving, before tipping
const unsigned long MOVE_TIMEOUT_MS = 4000;  // 180 deg takes ~1.1 s; anything near 4 s is wrong

// ===================== SERVO (same numbers as the known-good sketch) =====
const int SERVO_LEVEL = 90;
const int SERVO_TIP   = 90 + 40;       // tip RIGHT by 40 degrees
const unsigned long SERVO_STEP_MS = 15;   // ms per degree: slow, so items slide off
const unsigned long TIP_HOLD_MS   = 1000; // stay tipped while the item drops
const unsigned long SERVO_TIMEOUT_MS = 3000;  // 40 deg x 15 ms = 0.6 s normally

// ===================== IR SENSOR =========================================
// !!! PLACEHOLDERS - replace with the RESULT lines from ir_check.ino !!!
const bool ITEM_READS_HIGHER = true;   // RESULT direction=ITEM_HIGHER -> true
const int  IR_THRESHOLD      = 300;    // RESULT threshold=...
const int  IR_HYSTERESIS     = 20;     // RESULT hysteresis=...
const unsigned long IR_OBJECT_MS = 500; // item must be seen steadily this long
const unsigned long IR_CLEAR_MS  = 300; // platform must look empty this long
const unsigned long IR_SAMPLE_MS = 20;  // read the sensor 50 times a second

// ===================== STATE =============================================
enum Phase {
  IDLE,         // waiting; watching the IR sensor
  MOVE_OUT,     // rotating to the compartment
  SETTLE,       // short pause so the platform stops wobbling
  TIP_DOWN,     // servo tipping right
  TIP_HOLD,     // holding tipped while the item drops
  TIP_UP,       // servo returning level
  MOVE_HOME,    // rotating back to step 0
  STOPPED       // after STOP or an error: nothing moves until RESET
};
const char *PHASE_NAMES[] = {
  "IDLE", "MOVE_OUT", "SETTLE", "TIP_DOWN", "TIP_HOLD", "TIP_UP", "MOVE_HOME", "STOPPED"
};

Phase phase = IDLE;
char activeCmd[16] = "";       // command being carried out (for ACK/DONE)
long routeTarget = 0;

// stepper
long position = 0;             // current position in steps (0 = home)
long moveFrom = 0, moveTo = 0; // current move
long moveTotal = 0, moveDone = 0;
unsigned long nextStepUs = 0;
unsigned long phaseStartMs = 0;

// servo
Servo tipper;
int servoAngle = SERVO_LEVEL;
int servoTarget = SERVO_LEVEL;
unsigned long lastServoMs = 0;

// IR
bool irItem = false;           // our debounced view: is an item there?
int  irLast = 0;
unsigned long irChangeSince = 0;
unsigned long lastIrMs = 0;

// serial input
char rxBuf[33];
uint8_t rxLen = 0;
bool rxOverflow = false;

// ===================== HELPERS ===========================================

// Two separate functions (not one with a default argument): the Arduino IDE
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

// Step period for step number i of a move, same ramp as the known-good
// sketch: slow at both ends, fast in the middle.
unsigned long stepPeriod(long i, long total) {
  long ramp = total / 4;
  if (ramp < 1) ramp = 1;
  long fromEdge = min(i, total - 1 - i);
  if (fromEdge >= ramp) return FAST_US;
  return START_US - (START_US - FAST_US) * fromEdge / ramp;
}

void startMove(long target) {
  moveFrom = position;
  moveTo = target;
  moveTotal = labs(target - position);
  moveDone = 0;
  digitalWrite(PIN_DIR, (target > position) ? HIGH : LOW);
  delayMicroseconds(20);         // A4988 needs DIR settled before the first STEP
  nextStepUs = micros();
}

// Do at most one step if it is time. Returns true when the move is finished.
bool updateMove() {
  if ((long)(micros() - nextStepUs) < 0) return false;   // not time yet
  if (moveDone >= moveTotal) return true;                 // last step's wait is over

  digitalWrite(PIN_STEP, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_STEP, LOW);

  position += (moveTo > moveFrom) ? 1 : -1;   // count every step, so STOP keeps the position right
  nextStepUs = micros() + stepPeriod(moveDone, moveTotal);
  moveDone++;
  return false;
}

// Move the servo one degree towards servoTarget if it is time.
// Returns true when it has arrived.
bool updateServo() {
  if (servoAngle == servoTarget) return true;
  if (millis() - lastServoMs < SERVO_STEP_MS) return false;
  lastServoMs = millis();
  servoAngle += (servoTarget > servoAngle) ? 1 : -1;
  tipper.write(servoAngle);
  return servoAngle == servoTarget;
}

void haltMotors() {
  moveTotal = moveDone;          // abandon the stepper move (driver stays on and holds)
  servoTarget = servoAngle;      // freeze the servo where it is
}

void fail(const char *code) {
  haltMotors();
  enterPhase(STOPPED);
  reply2("ERR:", code);
}

bool timedOut(unsigned long limitMs) {
  return millis() - phaseStartMs > limitMs;
}

// ===================== THE SORT SEQUENCE =================================

void finishCommand() {
  enterPhase(IDLE);
  reply2("DONE:", activeCmd);
  activeCmd[0] = '\0';
  irChangeSince = millis();      // ignore IR readings taken while moving
}

void updateSequence() {
  switch (phase) {
    case IDLE:
    case STOPPED:
      break;

    case MOVE_OUT:
      if (updateMove()) enterPhase(SETTLE);
      else if (timedOut(MOVE_TIMEOUT_MS)) fail("TIMEOUT");
      break;

    case SETTLE:
      if (millis() - phaseStartMs >= SETTLE_MS) {
        servoTarget = SERVO_TIP;
        enterPhase(TIP_DOWN);
      }
      break;

    case TIP_DOWN:
      if (updateServo()) enterPhase(TIP_HOLD);
      else if (timedOut(SERVO_TIMEOUT_MS)) fail("TIMEOUT");
      break;

    case TIP_HOLD:
      if (millis() - phaseStartMs >= TIP_HOLD_MS) {
        servoTarget = SERVO_LEVEL;
        enterPhase(TIP_UP);
      }
      break;

    case TIP_UP:
      if (updateServo()) {
        startMove(0);              // level first, THEN rotate home
        enterPhase(MOVE_HOME);
      } else if (timedOut(SERVO_TIMEOUT_MS)) fail("TIMEOUT");
      break;

    case MOVE_HOME:
      if (updateMove()) finishCommand();
      else if (timedOut(MOVE_TIMEOUT_MS)) fail("TIMEOUT");
      break;
  }
}

// ===================== IR SENSOR =========================================

int readIR() {
  long sum = 0;
  for (int k = 0; k < 4; k++) sum += analogRead(PIN_IR);
  return (int)(sum / 4);
}

// Debounced: an item must be seen for IR_OBJECT_MS before OBJECT is sent,
// and the platform must look empty for IR_CLEAR_MS before CLEAR is sent.
// Hysteresis stops flicker when a reading sits right on the threshold.
void updateIR() {
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
  if (millis() - irChangeSince >= needed) {
    irItem = !irItem;
    reply(irItem ? "OBJECT" : "CLEAR");
  }
}

// ===================== COMMANDS ==========================================

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
    enterPhase(STOPPED);
    reply("STOPPED");
    return;
  }

  if (strcmp(cmd, "RESET") == 0) {
    if (phase != IDLE && phase != STOPPED) { reply("ERR:BUSY"); return; }
    strcpy(activeCmd, "RESET");
    reply2("ACK:", activeCmd);
    // Level the servo first, then go home: reuse the end of the sort sequence.
    servoTarget = SERVO_LEVEL;
    enterPhase(TIP_UP);
    return;
  }

  const Route *r = findRoute(cmd);
  if (r == NULL)        { reply("ERR:UNKNOWN_CMD"); return; }
  if (phase == STOPPED) { reply("ERR:STOPPED"); return; }
  if (phase != IDLE)    { reply("ERR:BUSY"); return; }

  strcpy(activeCmd, r->command);
  reply2("ACK:", activeCmd);
  routeTarget = r->target;
  startMove(routeTarget);        // 0 steps for PET: MOVE_OUT finishes at once
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
  digitalWrite(PIN_STEP, LOW);
  digitalWrite(PIN_ENABLE, LOW);   // driver on: holds the platform at HOME

  tipper.attach(PIN_SERVO);
  tipper.write(SERVO_LEVEL);

  analogReadResolution(10);        // 0..1023, same scale as ir_check.ino
  pinMode(PIN_IR, INPUT);

  Serial.begin(9600);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 2000) { }

  delay(500);                      // let the servo reach level
  irChangeSince = millis();
  Serial.println("# REPLAST controller v1");
  reply("READY");
}

void loop() {
  readSerial();       // first, so STOP is seen as early as possible
  updateSequence();
  updateIR();
}
