"""REPLAST main program: runs the whole sorting cycle on the Pi.

    IDLE -> Arduino says OBJECT -> take 5 photos -> run the model on each
         -> decide (decision.py) -> send SORT_* / REJECT -> wait for DONE
         -> check the item fell off (CLEAR) -> log the transaction -> IDLE

Run it on the Pi (from the pi/ folder, with the venv active):
    python3 -m replast.main
Stop it with Ctrl+C: the Arduino is told STOP so nothing keeps moving.

Safety rules built in:
  * If the camera or the model fails during a cycle, the item goes to
    REJECT (never to a material bin) and the cycle is logged as ERROR.
  * If the Arduino link fails (no ACK/DONE, ERR, cable pulled), the link has
    already sent STOP; the program logs the error and stops sorting.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from replast.camera import CameraError
from replast.config import DEFAULT_CONFIG_PATH, decision_params, load_config, serial_commands
from replast.decision import Decision, DecisionParams, Verdict, decide
from replast.detector import ModelError
from replast.logger import Transaction, TransactionLog
from replast.serial_link import SerialLink, SerialLinkError

log = logging.getLogger("replast")

PI_DIR = Path(__file__).resolve().parent.parent


class MachineHalted(RuntimeError):
    """The Arduino link failed; sorting must not continue until someone checks."""


class Machine:
    def __init__(
        self,
        link: SerialLink,
        camera: Any,
        detector: Any,
        params: DecisionParams,
        commands: dict[Verdict, str],
        tlog: TransactionLog,
        *,
        clear_timeout_s: float = 3.0,
        image_saver: Optional[Callable[[Sequence[Any], str], list]] = None,
        clock: Callable[[], float] = time.monotonic,
        say: Callable[[str], None] = print,
    ):
        self.link = link
        self.camera = camera
        self.detector = detector
        self.params = params
        self.commands = commands
        self.tlog = tlog
        self.clear_timeout_s = clear_timeout_s
        self.image_saver = image_saver
        self.clock = clock
        self.say = say

    def run_cycle(self, wait_s: float = 1.0) -> Optional[Transaction]:
        """Handle one item if one arrives within wait_s. Returns the logged row, or None.
        Raises MachineHalted if the Arduino link failed."""
        if not self.link.wait_for_object(wait_s):
            return None
        t0 = self.clock()
        self.say("Item detected - scanning...")
        t = Transaction()

        # --- see and decide -------------------------------------------------
        frames: Sequence[Any] = []
        decision: Optional[Decision] = None
        try:
            frames = self.camera.capture(self.params.n_frames)
            detections = self.detector.detect_many(frames)
            times = getattr(self.detector, "last_times_s", []) or []
            if times:
                t.inference_ms = round(1000 * sum(times) / len(times), 1)
            decision = decide(detections, self.params)
            verdict = decision.verdict
        except (CameraError, ModelError, ValueError) as e:
            # Can't see properly -> never guess a material bin.
            verdict = Verdict.REJECT
            t.outcome = "ERROR"
            t.error_msg = f"vision: {e}"
            self.say(f"  Vision problem ({e}) - sending item to REJECT")

        if decision is not None:
            t.reason_code = decision.reason.value
            t.reasons = ",".join(r.value for r in decision.reasons)
            t.winning_class = decision.winning_class or ""
            t.mean_conf = round(decision.mean_conf, 4)
            t.frames_detected = decision.frames_detected
            t.frames_agreeing = decision.frames_agreeing
            self.say(
                f"  Saw {decision.winning_class or 'nothing'}: "
                f"{decision.frames_detected}/{self.params.n_frames} frames, "
                f"{decision.frames_agreeing} agree, mean conf {decision.mean_conf:.2f} "
                f"-> {verdict.value} ({decision.reason.value})"
            )

        t.material = verdict.value
        t.command = self.commands[verdict]

        if self.image_saver is not None and frames:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            paths = self.image_saver(frames, f"{stamp}_{verdict.value}")
            t.images = ";".join(str(p) for p in paths)

        # --- move ----------------------------------------------------------
        t.response_time_s = round(self.clock() - t0, 3)
        try:
            self.link.run_command(t.command)
        except SerialLinkError as e:
            t.outcome = "ERROR"
            t.error_msg = (t.error_msg + "; " if t.error_msg else "") + f"mechanism: {e}"
            t.cycle_time_s = round(self.clock() - t0, 3)
            self.tlog.add(t)
            self.say(f"  MECHANISM PROBLEM: {e}. Motors stopped. Sorting halted.")
            raise MachineHalted(str(e)) from e

        t.item_cleared = int(self.link.wait_for_clear(self.clear_timeout_s))
        t.cycle_time_s = round(self.clock() - t0, 3)
        if not t.outcome:
            t.outcome = "REJECTED" if verdict is Verdict.REJECT else "SORTED"
        if not t.item_cleared:
            self.say("  Warning: the IR sensor still sees something - item may be stuck.")
        self.link.clear_pending_object()  # ignore any OBJECT caused by the move itself
        self.tlog.add(t)
        self.say(f"  {t.outcome} into {verdict.value} in {t.cycle_time_s:.1f} s "
                 f"(command sent after {t.response_time_s:.1f} s)")
        return t

    def run_forever(self) -> None:
        self.say("Ready. Place ONE item on the platform. Ctrl+C to stop.")
        while True:
            self.run_cycle(wait_s=1.0)


# ---------------------------------------------------------------------------
# Start-up with real hardware
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run the REPLAST sorter.")
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    p.add_argument("--debug", action="store_true", help="show every serial line")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")

    from replast.camera import camera_from_config, save_frames
    from replast.detector import detector_from_config
    from replast.serial_link import link_from_config

    cfg = load_config(args.config)
    params = decision_params(cfg)
    commands = serial_commands(cfg)
    m = cfg.get("machine", {})

    # Check everything BEFORE anything can move; any failure here exits cleanly.
    camera = link = None
    try:
        print("Loading model...")
        detector = detector_from_config(cfg)
        detector.warm_up(int(cfg["camera"]["width"]), int(cfg["camera"]["height"]))
        print("Opening camera...")
        camera = camera_from_config(cfg)
        camera.open()
        print(f"Connecting to Arduino on {cfg['serial']['port']}...")
        link = link_from_config(cfg)
        link.connect(float(cfg["serial"]["ready_timeout_s"]))
        # Clear any STOP left over from a previous run and make sure we're home.
        link.run_command("RESET")
    except Exception as e:  # model, camera, port: report and stop
        print(f"START-UP FAILED: {e}")
        if camera is not None:
            camera.close()
        if link is not None:
            link.stop()
            link.close()
        return 1

    tlog = TransactionLog(PI_DIR / m.get("db_path", "data/replast.db"))
    saver = None
    if m.get("save_images", True):
        folder = PI_DIR / m.get("image_dir", "data/captures")
        saver = lambda frames, stem: save_frames(list(frames), folder, stem)  # noqa: E731

    machine = Machine(link, camera, detector, params, commands, tlog,
                      clear_timeout_s=float(m.get("clear_timeout_s", 3.0)), image_saver=saver)
    code = 0
    try:
        machine.run_forever()
    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C).")
    except MachineHalted:
        code = 2
    finally:
        link.stop()   # whatever happened, leave the motors stopped
        link.close()
        camera.close()
        tlog.close()
        print(f"Log: {tlog.path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
