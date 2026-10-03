#pragma once
// Pretend U8x8 OLED driver for the host test: remembers the text on screen.
#include <string>
#include "Wire.h"
#define U8X8_PIN_NONE 255
extern std::string g_screen; extern int g_redraws;
static const unsigned char u8x8_font_chroma48medium8_r[1] = {0};
struct U8X8_SSD1306_128X64_NONAME_HW_I2C {
  explicit U8X8_SSD1306_128X64_NONAME_HW_I2C(int) {}
  void setI2CAddress(int) {} void setBusClock(long) {} void setFont(const unsigned char*) {}
  bool begin() { return true; }
  void clear() { g_screen.clear(); g_redraws++; }
  void drawString(int, int, const char* s) { g_screen += s; g_screen += "|"; }
  void draw2x2String(int, int, const char* s) { g_screen += s; g_screen += "|"; }
};
