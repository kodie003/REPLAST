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
    takes ~25 ms and would make the motor stutter). If the OLED is missing
    or broken, the machine still sorts.

  Wiring (docs/HARDWARE.md)
    STEP D9, DIR D10 (HIGH = clockwise), ENABLE D11 (LOW = on), A4988 at 1/16 microstep
    MG996R servo D6 (6 V from the buck converter, shared GND)
    Sharp IR sensor A2, passive buzzer D5, SSD1306 OLED on A4 (SDA) / A5 (SCL), address 0x3C

  Libraries (Arduino IDE -> Tools -> Manage Libraries):
    "Adafruit SSD1306" (say yes to installing Adafruit GFX and BusIO too).
    Servo and Wire come with the board.

  HOME = where the platform is at power-on. Line it up over the PET
  compartment (compartment 1) by hand BEFORE switching on.

  Serial: 9600 baud, one message per line (docs/PROTOCOL.md). You can also
  type the commands in the Serial Monitor (line ending "Newline").
*/

#include <Servo.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ===================== BIN MAP (the only place routes live) ==============
// Positions are in microsteps from HOME. 1/16 microstepping:
// 3200 microsteps per turn, 800 per compartment (90 degrees).
// Positive = clockwise (DIR HIGH), negative = counter-clockwise.
// Compartments are numbered counter-clockwise from home.
struct Route {
  const char *command;
  long target;          // where to rotate to before tipping
  const char *title;    // big word on the screen
  const char *detail;   // second line on the screen
};

const long STEPS_PER_BIN = 800;

const Route ROUTES[] = {
  { "SORT_PET",   0,                  "PLASTIC", "PET -> bin 1"       },
  { "SORT_PAPER", -1 * STEPS_PER_BIN, "PAPER",   "Paper -> bin 2"     },
  { "SORT_AL",    -2 * STEPS_PER_BIN, "METAL",   "Aluminium -> bin 3" },
  { "REJECT",     +1 * STEPS_PER_BIN, "REJECT",  "Unsure -> bin 4"    },
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
const float MAX_SPEED = 600.0;    // microsteps per second (reference sheet)
const float ACCEL     = 1200.0;   // microsteps per second^2 (reference sheet)
const unsigned long SETTLE_MS = 200;         // pause after arriving, before tipping
const unsigned long MOVE_TIMEOUT_MS = 6000;  // 2 bins takes ~3.2 s; 6 s means something is wrong

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

Adafruit_SSD1306 oled(128, 64, &Wire, -1);
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
  STOPPED       // after STOP or an error: nothing moves until RESET
};
const char *PHASE_NAMES[] = {
  "IDLE", "MOVE_OUT", "SETTLE", "TIP_DOWN", "TIP_HOLD", "TIP_UP", "MOVE_HOME", "STOPPED"
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

// Draw one screen: a big word on top and two small lines under it.
// Only call this when the stepper is not turning (see "Safety" above).
void showScreen(Screen s, const char *big, const char *line2, const char *line3) {
  screen = s;
  screenSince = millis();
  if (!oledOk) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(2);           // 10 characters per line
  oled.setCursor(0, 0);
  oled.println(big);
  oled.setTextSize(1);           // 21 characters per line
  oled.setCursor(0, 28);
  oled.println(line2);
  oled.setCursor(0, 44);
  oled.println(line3);
  oled.display();
}

void showReady()    { showScreen(SCR_READY, "READY", "Place ONE item", "on the platform"); }
void showItemIn()   { showScreen(SCR_ITEM, "ITEM IN", "Item detected", "Please wait..."); }
void showScanning() { showScreen(SCR_SCANNING, "SCANNING", "Camera is checking", "the material..."); }

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

// Time between two microsteps for step number i of a move. Speeds up at
// ACCEL until MAX_SPEED, cruises, then slows down the same way, so the
// platform starts and stops gently (v = sqrt(2 * a * distance)).
unsigned long stepPeriodUs(long i, long total) {
  long fromEdge = min(i, total - 1 - i);     // steps from the nearer end
  float v = sqrt(2.0 * ACCEL * (fromEdge + 1));
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
  showScreen(SCR_STOPPED, "ERROR", code, "Waiting for RESET");
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
    showScreen(SCR_STOPPED, "STOPPED", "Motors halted", "Waiting for RESET");
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

  analogReadResolution(10);        // 0..1023, same scale as ir_check.ino
  pinMode(PIN_IR, INPUT);

  Serial.begin(9600);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 2000) { }

  Wire.begin();
  Wire.setClock(400000);           // fast I2C: a full redraw takes ~25 ms instead of ~100 ms
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS);
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
