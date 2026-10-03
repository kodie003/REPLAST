#pragma once
extern int g_servo;
struct Servo{ void attach(int){} void write(int a){g_servo=a;} };
