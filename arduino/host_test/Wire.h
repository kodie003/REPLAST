#pragma once
// Pretend I2C bus for the host test. The OLED "answers" if g_oled_present.
extern bool g_oled_present;
struct TwoWire {
  void begin() {} void setClock(long) {}
  void beginTransmission(int) {}
  int endTransmission() { return g_oled_present ? 0 : 2; }
};
extern TwoWire Wire;
