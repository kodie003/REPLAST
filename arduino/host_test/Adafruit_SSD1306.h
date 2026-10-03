#pragma once
// Pretend OLED for the host test: remembers the text of the last screen drawn.
#include <string>
#include "Wire.h"
#define SSD1306_SWITCHCAPVCC 2
#define SSD1306_WHITE 1
extern std::string g_screen; extern int g_redraws; extern bool g_oled_present;
struct Adafruit_SSD1306 {
  std::string buf;
  Adafruit_SSD1306(int, int, TwoWire*, int) {}
  bool begin(int, int) { return g_oled_present; }
  void clearDisplay() { buf.clear(); }
  void setTextColor(int) {} void setTextSize(int) {} void setCursor(int, int) {}
  void println(const char* s) { buf += s; buf += "|"; }
  void display() { g_screen = buf; g_redraws++; }
};
