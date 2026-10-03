# REPLAST testing guide

## Level 1 — unit tests (laptop or Pi, no hardware)

One-time setup (from the repo root):

```bash
cd pi
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Run:

```bash
pytest -v          # every test, one line each
pytest --cov       # same, plus how much of the code the tests touched
```

Expected: a line ending `137 passed, 1 skipped` (the skipped one runs the real model and only runs where the model is installed) (the number grows as modules are added),
and `replast/decision.py ... 100%` in the coverage table.

### What is covered so far

| File | What it proves |
|---|---|
| `tests/test_decision.py` | Each reject rule on its own, every boundary (4/5 vs 3/5 detected, mean conf exactly 0.70, second object exactly 0.50, mass exactly 2 g), ties (2/2/1), empty frames, all-OTHER, unknown classes, several reasons at once, and a 5 000-case random fuzz test that a non-REJECT verdict always satisfies every rule. |
| `tests/test_serial_link.py` | Talking to a **simulated Arduino** (behaves like the real firmware) on a simulated clock: normal PING/sort/DONE, a lost PONG that is retried, missing ACK, missing DONE, every `ERR:` code, `STOPPED` mid-sort, junk and half-garbled lines, Windows line endings, cable pulled out. Every failed movement must be followed by `STOP`; after `STOP` nothing moves until `RESET`. |
| `tests/test_hil_sim.py` | Runs `hil_serial.py` and `hil_mechanism.py` against the simulator so the hardware scripts never silently break. |
| `tests/test_main.py` | The whole Pi cycle with a simulated camera, model and Arduino: each material reaches the right command and is logged; uncertain/OTHER items go to REJECT with the reason; camera or model failure sends the item to REJECT (never a material bin) and logs ERROR; no ACK/DONE/ERR stops the motors, logs ERROR and halts; a stuck item is flagged; 200 random items never reach a material bin without an OK decision. |
| `tests/test_logger.py` | The SQLite log stores and re-opens rows; CSV export round-trips. |
| `tests/test_vision.py` | Camera throws away stale frames before capturing, reports failures clearly; model class ids map to names; missing model folder gives a clear error; real-model test when available. |
| `tests/test_config.py` | The shipped `config.yaml` still holds the project-doc numbers (a regression guard), and bad values (e.g. `mean_conf: 1.5`) are refused at start-up. |

## Level 1b — firmware host test (no Arduino needed)

Compiles the **real** `replast_controller.ino` on the Pi/laptop with pretend
hardware and checks every route (rotation to the right bin, tip right, back home),
STOP, RESET, busy/unknown commands, the IR debounce, the buzzer beep and every
OLED screen, and that the screen is never redrawn while the motor is turning:

```bash
./arduino/host_test/run.sh
```

Expected last line: `ALL OK`. Run it after **any** change to the controller sketch.

## Level 3 — hardware-in-the-loop (real machine)

Before these: flash `arduino/replast_controller/replast_controller.ino`, close
the Serial Monitor, line the platform up over PET by hand, then power on.
Every script also runs with `--sim` (no hardware) so you can see what to expect.
Results go to `pi/results/*.csv`.

| Script | What it checks | Run |
|---|---|---|
| `tools/hil_camera.py` | Camera opens, frame rate ≥ 10 fps, correct size, not black/blown out; saves 3 sample photos to look at (`--show` for a live window) | `python3 tools/hil_camera.py` |
| `tools/hil_latency.py` | Model loads; for 10 items times 5 photos + 5 inferences + decision; prints what it saw; PASS if average ≤ 4.0 s | `python3 tools/hil_latency.py` |
| `tools/hil_serial.py` | 10 PINGs, STATUS, unknown command refused, each movement command gets ACK + DONE (with timings), STOP blocks movement until RESET | `python3 tools/hil_serial.py` |
| `tools/hil_mechanism.py` | Each route N times; after each route you confirm by eye it came back over PET and level; then STOP during a move must freeze the platform | `python3 tools/hil_mechanism.py --repeats 5` |

Expected final line for both: `...: PASS (N passed, 0 failed)`.

The camera scripts need no Arduino. Level 2 (offline model evaluation), the full-cycle acceptance test and the endurance test come in later steps.
