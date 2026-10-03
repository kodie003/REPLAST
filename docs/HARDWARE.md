# REPLAST hardware facts (single source of truth)

If a fact here changes on the real machine, update this file **first**, then the
code. Anything marked *TBD* has not been confirmed — do not guess it in code.

## Platform

| Item | Value | Source |
|---|---|---|
| Computer | Raspberry Pi 5 Model B Rev 1.0 | `cat /proc/device-tree/model`, 3 Oct 2026 |
| OS | Ubuntu 24.04.5 LTS (noble) → Python 3.12 | `lsb_release -a`, 3 Oct 2026 |
| Microcontroller | Arduino UNO R4 WiFi, USB serial to the Pi | project doc §7.1 |
| Serial port | `/dev/ttyACM0` | `ls /dev/ttyACM*`, 3 Oct 2026 |
| Baud | 9600 (matches every existing sketch) | |
| Camera | Logitech C270, OpenCV index 0, captured at 640×480 | `pi/known_good/test_camera.py` |
| Load cell | **none fitted** — the mass rule is disabled in `pi/config.yaml` | Selikem, 3 Oct 2026 |
| E-stop, limit switch, LEDs | **none fitted** (software `STOP` instead of an E-stop) | Selikem, 3 Oct 2026 |
| Buzzer, OLED | **fitted** (passive buzzer, SSD1306 128×64) | Component and pin reference, 3 Oct 2026 |

> Note: the project documentation PDF (§8, §9.1, Table 6/7) still says Pi 4 /
> Raspberry Pi OS. The machine is a Pi 5 on Ubuntu 24.04. The PDF should be updated.

## Arduino pins (from the team's component and pin reference — do not change without testing)

| Function | Pin | Notes |
|---|---|---|
| Stepper STEP | D9 | A4988, **1/16 microstepping** (MS1-3 tied to 5 V): 3200 microsteps/rev. Vref 0.56 V |
| Stepper DIR | D10 | **HIGH = positive steps = CLOCKWISE** (confirmed 3 Oct 2026: `r` turns clockwise) |
| Stepper ENABLE | D11 | LOW = driver on |
| Servo signal | D6 | MG996R, powered at 6 V from the LM2596 buck converter, grounds shared. Never from the Arduino 5 V pin |
| IR sensor (Sharp, analog) | A2 | Reading goes **up** when an item is present (to be confirmed by `ir_check.ino`) |
| Passive buzzer | D5 | Other leg to GND. Beeps once when an item is detected |
| OLED SDA / SCL | A4 / A5 | SSD1306, I2C address 0x3C |
| Reserved | D0, D1 | Hardware serial - keep unused |

## Bin map (confirmed by Selikem, 3 Oct 2026)

HOME (step 0) is wherever the platform sits when the Arduino powers on — line it
up over the PET compartment by hand before switching on. Compartments are
numbered counter-clockwise from home. One compartment (a quarter turn) is **800 microsteps**.
Top speed 600 microsteps/s, acceleration 1200 microsteps/s².

| Outcome | Compartment | Stepper move from HOME | Target position (steps) | Then |
|---|---|---|---|---|
| PET (model class `PLASTIC`) | 1 | none (already there) | 0 | tip right, level |
| PAPER (`PAPER`) | 2 | 90° counter-clockwise | −800 | tip right, level, return home |
| ALUMINIUM (`METAL`) | 3 | 180° counter-clockwise | −1600 | tip right, level, return home |
| REJECT (`OTHER` or any failed rule) | 4 | 90° clockwise | +800 | tip right, level, return home |

The servo **always tips right** (LEVEL 90° → 130°, i.e. `LEVEL + TILT_RIGHT`).

> Conflicts: the project doc Table 4 lists *Reject 0° (home), PET 90°, Paper 180°,
> Al −90°*, and the component reference lists *1 PET, 2 Aluminium, 3 Paper*.
> Selikem confirmed the table above (PET, Paper, Aluminium, Reject) on 3 Oct 2026.
> The reference's own serial protocol, points and 3-frame/0.60 model rule are
> **not** used: the tested protocol in `PROTOCOL.md` and the project-doc reject
> rule (5 frames, 4 agree, 0.70) stay (decided 3 Oct 2026).

## Known-good behaviour (baseline before any new firmware)

These are kept verbatim in `arduino/known_good/` and `pi/known_good/`. If the
new controller misbehaves, flash the matching known-good sketch to check whether
the problem is the hardware or the new code.

| File | Proven behaviour |
|---|---|
| `stepper_platform_test.ino` | `r` → +50 steps (90° clockwise), `l` → −50 steps, `0` → home. Ramped start/stop. Crossing sides always stops at home first. **Written for full-step wiring: now that MS1-3 are tied to 5 V, change `MICROSTEPS = 1` to `16` in a copy before using it, or it only turns 1/16 as far.** |
| `servo_tip_test.ino` | Level = 90°. `r` tips to 130°, holds 1 s, returns level. Moves 1°/15 ms so items slide rather than fly. |
| `ir_calibration.ino` | Averages 4 reads on A2; samples empty tray and items; prints `irThreshold` (midpoint) and `IR_HYSTERESIS` (gap/4, min 10). |
| `test_camera.py` | Opens camera 0 at 640×480, runs the NCNN model at `imgsz=512`, `conf=0.25`, prints class + confidence + latency. |

## Controller firmware

`arduino/replast_controller/replast_controller.ino` - the bin map lives in its
`ROUTES` table (and must match the table above). It also drives the buzzer and
OLED (screens listed in `PROTOCOL.md`). Needs the **Adafruit SSD1306** library
(Arduino IDE → Tools → Manage Libraries; accept Adafruit GFX and BusIO too). IR threshold values at the top
of that file are **placeholders until `arduino/tests/ir_check` results are in**.

## Model

- `finetuned_ncnn_model` from
  [Calvin_Patrick-Capstone-2026](https://github.com/ghanasnaplabel-a11y/Calvin_Patrick-Capstone-2026)
  (`Model/Yolov11/finetuned_ncnn_model.zip`).
- YOLO11n, trained 30 epochs on Ghanaian-Waste v7, Ultralytics 8.4.37.
- Class IDs: `0 METAL`, `1 OTHER`, `2 PAPER`, `3 PLASTIC` (uppercase).
- Exported at **imgsz 640**; `test_camera.py` and the project doc run it at **512**.
  Both work with NCNN; 512 is faster. Kept at 512 (configurable).
- This is Calvin's model trained on the public dataset. It has **not** yet been
  fine-tuned on REPLAST chamber photos (project doc §9.5 says that's needed
  before the acceptance test).
