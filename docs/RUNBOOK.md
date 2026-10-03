# REPLAST runbook - starting and stopping the machine

## Before switching on
1. Platform empty, and lined up by hand so **bin 1 (PET)** is in position.
   (There is no limit switch: wherever it is at power-on is "home".)
2. Camera (C270) and Arduino both plugged into the Pi's USB ports.
3. 12 V supply off.

## Power-on order
1. Pi on (USB-C power bank). Wait for the desktop.
2. 12 V supply on (motor driver + servo buck converter).
3. The Arduino is powered from the Pi's USB: its screen shows **READY**.

## Quick self-test (2 minutes)
Open a terminal:
```bash
cd ~/REPLAST/pi
source .venv/bin/activate
python3 tools/hil_serial.py          # moves the platform: hands clear
```
Expected last line: `hil_serial: PASS (12 passed, 0 failed)`.

## Start sorting
```bash
python3 -m replast.main
```
Expected:
```
Loading model...
Opening camera...
Connecting to Arduino on /dev/ttyACM0...
Ready. Place ONE item on the platform. Ctrl+C to stop.
```
Then for each item the terminal prints what the camera saw and where it went,
and the OLED tells the user (ITEM IN -> SCANNING -> material -> DROPPING -> SORTED).

Every item is logged in `pi/data/replast.db`; its 5 photos are saved in `pi/data/captures/`.

## Stop safely
* **Ctrl+C** in the terminal: the Pi sends STOP, motors halt, the program exits.
* Then switch off the 12 V supply, then the Pi (`sudo poweroff`).

## If something goes wrong
| You see | Do |
|---|---|
| `START-UP FAILED: model folder not found` | Fix `model.path` in `pi/config.yaml` |
| `START-UP FAILED: cannot open camera` | Re-plug the C270; `ls /dev/video*` should list `/dev/video0` |
| `Permission denied: '/dev/ttyACM0'` | `sudo usermod -aG dialout $USER`, then reboot |
| `Arduino did not answer PING` | Close the Arduino IDE Serial Monitor; re-plug the Arduino |
| `MECHANISM PROBLEM ... Sorting halted` | Motors are stopped. Check for a jam, line the platform up over bin 1, restart `python3 -m replast.main` (it sends RESET at start-up) |
| `item may be stuck` | Remove the item by hand before placing the next one |
| Screen shows STOPPED / ERROR | Restart `python3 -m replast.main` (sends RESET) |
