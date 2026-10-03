#pragma once
// Pretend I2C bus for the host test.
struct TwoWire { void begin(){} void setClock(long){} };
extern TwoWire Wire;
