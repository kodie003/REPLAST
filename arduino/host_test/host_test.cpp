// Runs the REAL replast_controller.ino on a laptop/Pi with pretend hardware
// (Arduino.h / Servo.h in this folder) and checks every route, STOP, RESET,
// busy/unknown commands and the IR debounce. See run.sh.
#include "Arduino.h"
#include "Servo.h"
#include <cstdio>
#include <cassert>
uint64_t g_us=0; int g_ir=100; long g_steps=0; int g_dir=0; int g_pins[32]={0}; int g_servo=-1;
SerialMock Serial;
// Pull in the real sketch unchanged.
#include "../replast_controller/replast_controller.ino"
int maxServo=0; long minSteps=0,maxSteps=0;
std::string runFor(double sec){ uint64_t end=g_us+(uint64_t)(sec*1e6); Serial.out.clear();
  while(g_us<end){ loop(); g_us+=100; maxServo=std::max(maxServo,g_servo); minSteps=std::min(minSteps,g_steps); maxSteps=std::max(maxSteps,g_steps);} return Serial.out; }
std::string cmd(const char*c,double sec){ Serial.send(std::string(c)+"\n"); return runFor(sec); }
double timeTo(const char* c, const char* want){ Serial.send(std::string(c)+"\n"); uint64_t t0=g_us; Serial.out.clear();
  while(Serial.out.find(want)==std::string::npos && g_us-t0<20e6){ loop(); g_us+=100; maxServo=std::max(maxServo,g_servo); minSteps=std::min(minSteps,g_steps); maxSteps=std::max(maxSteps,g_steps);} return (g_us-t0)/1e6; }
#define CHECK(x) do{ if(!(x)){printf("FAIL line %d: %s\n",__LINE__,#x); fails++;} }while(0)
int main(){ int fails=0;
  setup(); std::string o=Serial.out; CHECK(o.find("READY")!=std::string::npos); CHECK(g_servo==90);
  o=cmd("PING",0.1); CHECK(o=="PONG\n");
  struct {const char*c; long t;} r[]={{"SORT_PET",0},{"SORT_PAPER",-50},{"SORT_AL",-100},{"REJECT",50}};
  for(auto&x:r){ maxServo=0;minSteps=0;maxSteps=0; double t=timeTo(x.c,(std::string("DONE:")+x.c).c_str());
    printf("%-11s DONE after %.2fs  extreme steps [%ld,%ld] max servo %d  end steps %ld servo %d | %s", x.c,t,minSteps,maxSteps,maxServo,g_steps,g_servo, Serial.out.c_str());
    CHECK(g_steps==0); CHECK(g_servo==90); CHECK(maxServo==130); CHECK((x.t<0?minSteps:maxSteps)==x.t); CHECK(Serial.out.find(std::string("ACK:")+x.c)==0); }
  o=cmd("sort_glass",0.1); CHECK(o=="ERR:UNKNOWN_CMD\n");
  // busy
  Serial.send("SORT_AL\n"); runFor(0.3); o=cmd("REJECT",0.05); CHECK(o.find("ERR:BUSY")!=std::string::npos);
  o=cmd("PING",0.05); CHECK(o.find("PONG")!=std::string::npos);
  // STOP mid-move
  o=cmd("STOP",0.05); CHECK(o.find("STOPPED")!=std::string::npos); long held=g_steps; printf("stopped at step %ld\n",held);
  runFor(2); CHECK(g_steps==held); CHECK(held<0 && held>-100);
  o=cmd("SORT_PET",0.1); CHECK(o=="ERR:STOPPED\n");
  double t=timeTo("RESET","DONE:RESET"); printf("RESET took %.2fs, steps %ld servo %d\n",t,g_steps,g_servo); CHECK(g_steps==0); CHECK(g_servo==90);
  // STOP while tipped, then RESET levels first
  Serial.send("SORT_PET\n"); runFor(0.6); printf("servo mid-tip %d\n",g_servo); cmd("STOP",0.05); int frozen=g_servo; runFor(1); CHECK(g_servo==frozen); CHECK(frozen>90);
  timeTo("RESET","DONE:RESET"); CHECK(g_servo==90);
  // IR: flicker shorter than 500ms -> nothing; steady -> OBJECT once; removed -> CLEAR
  g_ir=400; o=runFor(0.3); g_ir=100; o+=runFor(0.5); CHECK(o.find("OBJECT")==std::string::npos);
  g_ir=400; o=runFor(1.5); CHECK(o=="OBJECT\n");
  o=runFor(2); CHECK(o=="");
  g_ir=100; o=runFor(1); CHECK(o=="CLEAR\n");
  // IR ignored while moving; item falls during sort -> CLEAR afterwards
  g_ir=400; runFor(1); Serial.send("SORT_PAPER\n"); runFor(1.0); g_ir=100; o=runFor(4); printf("sort with item: %s",o.c_str()); CHECK(o.find("DONE:SORT_PAPER")<o.find("CLEAR"));
  // overflow line
  o=cmd("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",0.05); CHECK(o=="ERR:UNKNOWN_CMD\n");
  o=cmd("STATUS",0.05); printf("%s",o.c_str());
  printf(fails? "%d FAILURES\n":"ALL OK\n",fails); return fails!=0; }
