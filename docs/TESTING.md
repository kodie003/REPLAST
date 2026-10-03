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

Expected: a line ending `107 passed` (the number grows as modules are added),
and `replast/decision.py ... 100%` in the coverage table.

### What is covered so far

| File | What it proves |
|---|---|
| `tests/test_decision.py` | Each reject rule on its own, every boundary (4/5 vs 3/5 detected, mean conf exactly 0.70, second object exactly 0.50, mass exactly 2 g), ties (2/2/1), empty frames, all-OTHER, unknown classes, several reasons at once, and a 5 000-case random fuzz test that a non-REJECT verdict always satisfies every rule. |
| `tests/test_serial_link.py` | Talking to a **fake Arduino** on a fake clock: normal PING/sort/DONE, a lost PONG that is retried, missing ACK, missing DONE, every `ERR:` code, `STOPPED` mid-sort, junk and half-garbled lines, Windows line endings, cable pulled out. Every failed movement must be followed by `STOP`. |
| `tests/test_config.py` | The shipped `config.yaml` still holds the project-doc numbers (a regression guard), and bad values (e.g. `mean_conf: 1.5`) are refused at start-up. |

Levels 2 (offline model evaluation) and 3 (hardware-in-the-loop) will be added
in later steps.
