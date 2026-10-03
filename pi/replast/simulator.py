"""Simulated hardware for tests and laptop dry-runs. Nothing here touches real devices.

SimulatedArduino answers exactly like arduino/replast_controller (see
docs/PROTOCOL.md), on a simulated clock, so a 4-second sort finishes
instantly in a test. Route timings come from the firmware host test
(arduino/host_test).
"""

from __future__ import annotations

from typing import Optional, Sequence, Union

# A scripted reply: (seconds after the command arrives, line to send back).
Reply = tuple[float, str]

# Seconds from command to DONE, measured by running the real firmware in
# arduino/host_test (rotate out + settle + tip + hold + level + rotate home).
ROUTE_SECONDS = {
    "SORT_PET": 2.7,
    "SORT_PAPER": 3.7,
    "SORT_AL": 3.7,
    "REJECT": 4.5,
    "RESET": 0.5,
}


class SimClock:
    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class SimulatedArduino:
    """A serial-port look-alike. Pass it to SerialLink(port=..., clock=sim.clock).

    overrides: {command: [replies]} replaces the normal answer for that
    command. An empty list means "say nothing" (simulates a lost reply).
    A list of lists is used one entry per call (e.g. first PING lost, second answered).
    """

    def __init__(
        self,
        clock: Optional[SimClock] = None,
        overrides: Optional[dict[str, Union[Sequence[Reply], Sequence[Sequence[Reply]]]]] = None,
        read_timeout_s: float = 0.1,
    ):
        self.clock = clock or SimClock()
        self.overrides = dict(overrides or {})
        self.read_timeout_s = read_timeout_s
        self.written: list[str] = []
        self._outbox: list[tuple[float, int, bytes]] = []  # (time ready, order, data)
        self._seq = 0
        self._calls: dict[str, int] = {}
        self.stopped = False       # like the firmware: after STOP, only RESET moves
        self.busy_until = 0.0
        self.closed = False
        self.fail_writes = False
        self.fail_reads = False

    # ---- test helpers ----

    def push(self, line: Union[str, bytes], delay: float = 0.0) -> None:
        """Make the Arduino send a line by itself (e.g. OBJECT) after `delay` seconds."""
        data = line if isinstance(line, bytes) else (line + "\n").encode("ascii")
        self._seq += 1
        self._outbox.append((self.clock() + delay, self._seq, data))
        self._outbox.sort()

    def commands(self) -> list[str]:
        return list(self.written)

    # ---- serial-port interface ----

    def write(self, data: bytes) -> int:
        if self.fail_writes:
            raise OSError("simulated: device disconnected")
        for line in data.decode("ascii").splitlines():
            line = line.strip()
            if not line:
                continue
            self.written.append(line)
            for delay, reply in self._replies_for(line):
                self.push(reply, delay)
        return len(data)

    def readline(self) -> bytes:
        if self.fail_reads:
            raise OSError("simulated: device disconnected")
        if self._outbox and self._outbox[0][0] <= self.clock():
            return self._outbox.pop(0)[2]
        # Nothing ready: behave like pyserial and block for the read timeout,
        # returning early if a line becomes ready within it.
        if self._outbox and self._outbox[0][0] <= self.clock() + self.read_timeout_s:
            self.clock.t = self._outbox[0][0]
            return self._outbox.pop(0)[2]
        self.clock.advance(self.read_timeout_s)
        return b""

    def close(self) -> None:
        self.closed = True

    # ---- firmware behaviour ----

    def _replies_for(self, cmd: str) -> Sequence[Reply]:
        n = self._calls.get(cmd, 0)
        self._calls[cmd] = n + 1
        if cmd in self.overrides:
            script = self.overrides[cmd]
            # A list of lists means "a different answer on each call".
            if script and isinstance(script[0], list):
                if n < len(script):
                    return script[n]  # type: ignore[return-value]
            else:
                return script  # type: ignore[return-value]
        return self._firmware_reply(cmd.upper())

    def _firmware_reply(self, cmd: str) -> list[Reply]:
        busy = self.clock() < self.busy_until
        if cmd == "PING":
            return [(0.01, "PONG")]
        if cmd == "STATUS":
            state = "STOPPED" if self.stopped else ("MOVING" if busy else "IDLE")
            return [(0.01, f"STATUS:{state},POS=0,SERVO=90,IR=100,ITEM=0")]
        if cmd == "STOP":
            self.stopped = True
            self.busy_until = 0.0
            # A halted move never finishes, so its DONE must never arrive.
            self._outbox = [o for o in self._outbox if not o[2].startswith(b"DONE:")]
            return [(0.01, "STOPPED")]
        if cmd == "RESET":
            if busy and not self.stopped:
                return [(0.01, "ERR:BUSY")]
            self.stopped = False
            return self._start(cmd)
        if cmd not in ROUTE_SECONDS:
            return [(0.01, "ERR:UNKNOWN_CMD")]
        if self.stopped:
            return [(0.01, "ERR:STOPPED")]
        if busy:
            return [(0.01, "ERR:BUSY")]
        return self._start(cmd)

    def _start(self, cmd: str) -> list[Reply]:
        duration = ROUTE_SECONDS[cmd]
        self.busy_until = self.clock() + duration
        return [(0.02, f"ACK:{cmd}"), (duration, f"DONE:{cmd}")]


class SimulatedCamera:
    """Returns placeholder 'frames' (just numbers) instead of photos."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.captures = 0

    def open(self) -> None:
        pass

    def capture(self, n: int) -> list:
        from replast.camera import CameraError

        if self.fail:
            raise CameraError("simulated: camera unplugged")
        self.captures += 1
        return list(range(n))

    def close(self) -> None:
        pass


class SimulatedDetector:
    """Returns pre-set detections instead of running the model.

    script: one entry per item; each entry is the list of per-frame detection
    lists to return for that item.
    """

    def __init__(self, script: list, fail: bool = False):
        self.script = list(script)
        self.fail = fail
        self.last_times_s: list[float] = []

    def detect_many(self, frames: list) -> list:
        from replast.detector import ModelError

        if self.fail:
            raise ModelError("simulated: inference failed")
        self.last_times_s = [0.1] * len(frames)
        return self.script.pop(0)
