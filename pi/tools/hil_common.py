"""Shared helpers for the hardware-in-the-loop (HIL) scripts.

Every HIL script:
  * prints one PASS/FAIL line per check and a summary at the end,
  * saves its measurements to pi/results/<script>_<date-time>.csv,
  * accepts --sim to run against the simulated Arduino (no hardware needed),
  * exits with code 0 only if every check passed.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

PI_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PI_DIR))

from replast.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from replast.serial_link import LinkDeadError, SerialLink, link_from_config  # noqa: E402
from replast.simulator import SimClock, SimulatedArduino  # noqa: E402

RESULTS_DIR = PI_DIR / "results"


def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to config.yaml")
    p.add_argument("--port", help="serial port (default: from config.yaml)")
    p.add_argument("--sim", action="store_true", help="use the simulated Arduino instead of real hardware")
    p.add_argument("--yes", action="store_true", help="answer yes to every question (unattended run)")
    return p


class HilRun:
    def __init__(self, name: str, args: argparse.Namespace):
        self.name = name
        self.args = args
        self.cfg = load_config(args.config)
        if args.port:
            self.cfg["serial"]["port"] = args.port
        self.passed = 0
        self.failed = 0
        self.rows: list[dict[str, Any]] = []
        self.sim_clock: Optional[SimClock] = None
        self.link: Optional[SerialLink] = None

    # ---- connection ----

    def connect(self) -> SerialLink:
        if self.args.sim:
            self.sim_clock = SimClock()
            port = SimulatedArduino(self.sim_clock)
            port.push("READY", 0.5)
            link = link_from_config(self.cfg, port=port)
            link.clock = self.sim_clock
        else:
            print(f"Opening {self.cfg['serial']['port']} at {self.cfg['serial']['baud']} baud...")
            try:
                link = link_from_config(self.cfg)
            except Exception as e:  # pyserial raises its own SerialException
                self.check("open serial port", False, str(e))
                print_port_help()
                self.finish()
        try:
            link.connect(float(self.cfg["serial"]["ready_timeout_s"]))
            self.check("Arduino answers PING", True)
        except LinkDeadError as e:
            self.check("Arduino answers PING", False, str(e))
            print("  Is replast_controller.ino flashed? Is the Serial Monitor closed?")
            self.finish()
        self.link = link
        return link

    def recover(self) -> bool:
        """After a failed command: try to get the platform back home. Never raises."""
        try:
            if self.link is not None and self.link.ping():
                self.link.run_command("RESET")
                return True
        except Exception as e:  # report and carry on to the next check
            print(f"  RESET after failure did not complete: {e}")
        return False

    def now(self) -> float:
        return self.sim_clock() if self.sim_clock else time.monotonic()

    def wait(self, seconds: float) -> None:
        if self.sim_clock:
            self.sim_clock.advance(seconds)
        else:
            time.sleep(seconds)

    # ---- reporting ----

    def check(self, what: str, ok: bool, detail: str = "") -> bool:
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {what}" + (f"  ({detail})" if detail else ""))
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        return ok

    def record(self, **row: Any) -> None:
        self.rows.append({"time": datetime.now().isoformat(timespec="seconds"), **row})

    def ask(self, question: str) -> bool:
        if self.args.yes or self.args.sim:
            print(f"{question} [y/n] y (auto)")
            return True
        while True:
            a = input(f"{question} [y/n] ").strip().lower()
            if a in ("y", "yes"):
                return True
            if a in ("n", "no"):
                return False

    def finish(self) -> None:
        """Save the CSV, print the summary, put the Arduino in a safe state and exit."""
        if self.link is not None:
            self.link.stop()   # leave nothing able to move
            self.link.close()
        path = None
        if self.rows:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = "_sim" if self.args.sim else ""
            path = RESULTS_DIR / f"{self.name}{suffix}_{stamp}.csv"
            keys: list[str] = []
            for r in self.rows:
                keys += [k for k in r if k not in keys]
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(self.rows)
        print()
        print("=" * 50)
        verdict = "PASS" if self.failed == 0 else "FAIL"
        print(f"{self.name}: {verdict}  ({self.passed} passed, {self.failed} failed)")
        if path:
            print(f"Results saved to {path}")
        print("=" * 50)
        sys.exit(0 if self.failed == 0 else 1)


def print_port_help() -> None:
    print(
        "  Check:  ls /dev/ttyACM*        (is the Arduino plugged in?)\n"
        "          groups                 (does it list 'dialout'? if not:\n"
        "          sudo usermod -aG dialout $USER   then reboot)\n"
        "          Close the Arduino IDE Serial Monitor - only one program can use the port."
    )
