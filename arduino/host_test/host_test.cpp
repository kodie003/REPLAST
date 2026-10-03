// Runs the REAL replast_controller.ino on a laptop/Pi with pretend hardware
// (the .h files in this folder) and checks: every route, tip, return home,
// STOP, RESET, busy/unknown commands, IR debounce, the buzzer and the screens.
// See run.sh. Expected last line: ALL OK
#include "Arduino.h"
#include "Servo.h"
#include "Wire.h"
#include "Adafruit_SSD1306.h"
#include <cstdio>

uint64_t g_us = 0; int g_ir = 100; long g_steps = 0; int g_dir = 0; int g_pins[32] = {0};
int g_servo = -1; int g_beeps = 0; std::string g_screen; int g_redraws = 0; bool g_oled_present = true;
SerialMock Serial;
TwoWire Wire;
int g_redraws_while_turning = 0;
void (*g_on_redraw)() = nullptr;

// Pull in the real sketch unchanged.
#include "../replast_controller/replast_controller.ino"

int maxServo = 0; long minSteps = 0, maxSteps = 0;

void tick() {
  int before = g_redraws;
  // A redraw in the middle of a stepper move would make the motor stutter.
  // (Drawing right before the first step, or right after STOP halted it, is fine.)
  auto turning = [] { return (phase == MOVE_OUT || phase == MOVE_HOME) && moveDone > 0 && moveDone < moveTotal; };
  bool wasTurning = turning();
  loop();
  if (wasTurning && turning() && g_redraws != before) g_redraws_while_turning++;
  g_us += 100;
  maxServo = std::max(maxServo, g_servo);
  minSteps = std::min(minSteps, g_steps);
  maxSteps = std::max(maxSteps, g_steps);
}
std::string runFor(double sec) {
  uint64_t end = g_us + (uint64_t)(sec * 1e6);
  Serial.out.clear();
  while (g_us < end) tick();
  return Serial.out;
}
std::string cmd(const char *c, double sec) { Serial.send(std::string(c) + "\n"); return runFor(sec); }
double timeTo(const char *c, const char *want) {
  Serial.send(std::string(c) + "\n");
  uint64_t t0 = g_us;
  Serial.out.clear();
  while (Serial.out.find(want) == std::string::npos && g_us - t0 < 30e6) tick();
  return (g_us - t0) / 1e6;
}
bool on(const char *word) { return g_screen.find(word) != std::string::npos; }

#define CHECK(x) do { if (!(x)) { printf("FAIL line %d: %s   [screen: %s]\n", __LINE__, #x, g_screen.c_str()); fails++; } } while (0)

int main() {
  int fails = 0;
  setup();
  std::string o = Serial.out;
  CHECK(o.find("READY") != std::string::npos);
  CHECK(g_servo == 90);
  CHECK(on("READY"));

  o = cmd("PING", 0.1); CHECK(o == "PONG\n");

  // --- 1 + 2: every route rotates to the right bin, tips right, comes home ---
  struct { const char *c; long target; const char *title; } routes[] = {
    {"SORT_PET", 0, "PLASTIC"}, {"SORT_PAPER", -800, "PAPER"}, {"SORT_AL", -1600, "METAL"}, {"REJECT", 800, "REJECT"}};
  for (auto &r : routes) {
    maxServo = 0; minSteps = 0; maxSteps = 0;
    Serial.send(std::string(r.c) + "\n");
    tick(); tick();
    CHECK(on(r.title));                    // screen names the material before moving
    double t = timeTo("", (std::string("DONE:") + r.c).c_str());
    printf("%-11s DONE after %.2fs  reached %ld  max servo %d  end pos %ld servo %d\n",
           r.c, t, r.target < 0 ? minSteps : maxSteps, maxServo, g_steps, g_servo);
    CHECK(g_steps == 0);
    CHECK(g_servo == 90);
    CHECK(maxServo == 130);
    CHECK((r.target < 0 ? minSteps : maxSteps) == r.target);
    CHECK(on("SORTED"));
    runFor(3.0);
    CHECK(on("READY"));                    // back to READY after 2.5 s
  }
  CHECK(g_redraws_while_turning == 0);

  o = cmd("sort_glass", 0.1); CHECK(o == "ERR:UNKNOWN_CMD\n");

  // busy
  Serial.send("SORT_AL\n"); runFor(0.5);
  o = cmd("REJECT", 0.05); CHECK(o.find("ERR:BUSY") != std::string::npos);
  o = cmd("PING", 0.05); CHECK(o.find("PONG") != std::string::npos);

  // STOP mid-move freezes the stepper and blocks movement until RESET
  o = cmd("STOP", 0.05); CHECK(o.find("STOPPED") != std::string::npos);
  CHECK(on("STOPPED"));
  long held = g_steps; printf("stopped at microstep %ld\n", held);
  runFor(2); CHECK(g_steps == held); CHECK(held < 0 && held > -1600);
  o = cmd("SORT_PET", 0.1); CHECK(o == "ERR:STOPPED\n");
  double t = timeTo("RESET", "DONE:RESET");
  printf("RESET took %.2fs, pos %ld servo %d\n", t, g_steps, g_servo);
  CHECK(g_steps == 0); CHECK(g_servo == 90); CHECK(on("READY"));

  // STOP while tipped freezes the servo; RESET levels it first
  Serial.send("SORT_PET\n"); runFor(0.6);
  cmd("STOP", 0.05); int frozen = g_servo; runFor(1);
  CHECK(g_servo == frozen); CHECK(frozen > 90);
  timeTo("RESET", "DONE:RESET"); CHECK(g_servo == 90);

  // --- 3: IR + buzzer ---
  int beeps0 = g_beeps;
  g_ir = 400; o = runFor(0.3); g_ir = 100; o += runFor(0.5);
  CHECK(o.find("OBJECT") == std::string::npos);    // a quick wave is not an item
  CHECK(g_beeps == beeps0);
  g_ir = 400; o = runFor(0.6);
  CHECK(o == "OBJECT\n");
  CHECK(g_beeps == beeps0 + 1);                    // beep once on a new item
  CHECK(on("ITEM IN"));
  // --- 4: screen moves on to SCANNING while the Pi decides ---
  runFor(1.0); CHECK(on("SCANNING"));
  o = runFor(2); CHECK(o == ""); CHECK(g_beeps == beeps0 + 1);   // no repeat beeps
  g_ir = 100; o = runFor(1); CHECK(o == "CLEAR\n");
  CHECK(on("READY"));                              // item taken away before sorting

  // IR ignored while moving; item falls during sort -> CLEAR after DONE
  g_ir = 400; runFor(1); Serial.send("SORT_PAPER\n"); runFor(1.0); g_ir = 100; o = runFor(8);
  CHECK(o.find("DONE:SORT_PAPER") != std::string::npos);
  CHECK(o.find("DONE:SORT_PAPER") < o.find("CLEAR"));

  // overflow line
  o = cmd("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", 0.05); CHECK(o == "ERR:UNKNOWN_CMD\n");
  o = cmd("STATUS", 0.05); printf("%s", o.c_str());
  CHECK(g_redraws_while_turning == 0);

  printf(fails ? "%d FAILURES\n" : "ALL OK\n", fails);
  return fails != 0;
}
