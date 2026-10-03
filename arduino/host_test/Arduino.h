#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <deque>
#include <algorithm>
#include <cmath>
using std::sqrt;
using std::min;
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT 0
#define A2 16
extern uint64_t g_us; extern int g_ir; extern long g_steps; extern int g_dir; extern int g_pins[32];
inline unsigned long millis(){return (unsigned long)(g_us/1000);}
inline unsigned long micros(){return (unsigned long)g_us;}
inline void delay(unsigned long ms){g_us+=ms*1000ULL;}
inline void delayMicroseconds(unsigned int us){g_us+=us;}
inline void pinMode(int,int){}
inline void digitalWrite(int p,int v){ if(p==9 && v==HIGH && g_pins[9]==LOW){ g_steps += g_dir? 1:-1; } if(p==10) g_dir=v; g_pins[p]=v; }
inline int analogRead(int){return g_ir;}
extern int g_beeps;
inline void tone(int,unsigned int,unsigned long){g_beeps++;}
inline void analogReadResolution(int){}
struct SerialMock{
  std::deque<char> in; std::string out;
  void begin(long){} explicit operator bool(){return true;}
  int available(){return in.size();}
  int read(){char c=in.front(); in.pop_front(); return c;}
  void print(const char*s){out+=s;} void print(long v){out+=std::to_string(v);} void print(int v){out+=std::to_string(v);}
  void println(const char*s){out+=s; out+="\n";} void println(long v){print(v); out+="\n";} void println(int v){print(v); out+="\n";}
  void send(const std::string&s){for(char c:s) in.push_back(c);}
};
extern SerialMock Serial;
