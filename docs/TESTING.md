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

Expected: a line ending `113 passed` (the number grows as modules are added),
and `replast/decision.py ... 100%` in the coverage table.

### What is covered so far

| File | What it proves |
|---|---|
| `tests/test_decision.py` | Each reject rule on its own, every boundary (4/5 vs 3/5 detected, mean conf exactly 0.70, second object exactly 0.50, mass exactly 2 g), ties (2/2/1), empty frames, all-OTHER, unknown classes, several reasons at once, and a 5 000-case random fuzz test that a non-REJECT verdict always satisfies every rule. |
| `tests/test_serial_link.py` | Talking to a **simulated Arduino** (behaves like the real firmware) on a simulated clock: normal PING/sort/DONE, a lost PONG that is retried, missing ACK, missing DONE, every `ERR:` code, `STOPPED` mid-sort, junk and half-garbled lines, Windows line endings, cable pulled out. Every failed movement must be followed by `STOP`; after `STOP` nothing moves until `RESET`. |
| `tests/test_hil_sim.py` | Runs `hil_serial.py` and `hil_mechanism.py` against the simulator so the hardware scripts never silently break. |
| `tests/test_config.py` | The shipped `config.yaml` still holds the project-doc numbers (a regression guard), and bad values (e.g. `mean_conf: 1.5`) are refused at start-up. |

## Level 1b — firmware host test (no Arduino needed)

Compiles the **real** `replast_controller.ino` on the Pi/laptop with pretend
hardware and checks every route, STOP, RESET, busy/unknown commands and the IR
debounce:

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
| `tools/hil_serial.py` | 10 PINGs, STATUS, unknown command refused, each movement command gets ACK + DONE (with timings), STOP blocks movement until RESET | `python3 tools/hil_serial.py` |
| `tools/hil_mechanism.py` | Each route N times; after each route you confirm by eye it came back over PET and level; then STOP during a move must freeze the platform | `python3 tools/hil_mechanism.py --repeats 5` |

Expected final line for both: `...: PASS (N passed, 0 failed)`.

Level 2 (offline model evaluation) and the remaining HIL scripts come in later steps.
