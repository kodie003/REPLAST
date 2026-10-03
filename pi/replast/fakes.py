"""Fake hardware for tests and laptop dry-runs. Nothing here touches real devices.

FakeArduino behaves like the controller firmware described in docs/PROTOCOL.md,
on a fake clock, so a 3-second sort finishes instantly in a test.
"""

from __future__ import annotations

from typing import Optional, Sequence, Union

# A scripted reply: (seconds after the command arrives, line to send back).
Reply = tuple[float, str]


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def default_replies(cmd: str) -> list[Reply]:
    """What a healthy Arduino would answer to each command."""
    if cmd == "PING":
        return [(0.01, "PONG")]
    if cmd == "STOP":
        return [(0.01, "STOPPED")]
    if cmd in ("SORT_PET", "SORT_PAPER", "SORT_AL", "REJECT", "RESET"):
        return [(0.02, f"ACK:{cmd}"), (3.0, f"DONE:{cmd}")]
    return [(0.01, "ERR:UNKNOWN_CMD")]


class FakeArduino:
    """A serial-port look-alike. Pass it to SerialLink(port=..., clock=fake.clock).

    overrides: {command: [replies]} replaces the default answer for that
    command. An empty list means "say nothing" (simulates a lost reply).
    A list of lists is used one entry per call (e.g. first PING lost, second answered).
    """

    def __init__(
        self,
        clock: Optional[FakeClock] = None,
        overrides: Optional[dict[str, Union[Sequence[Reply], Sequence[Sequence[Reply]]]]] = None,
        read_timeout_s: float = 0.1,
    ):
        self.clock = clock or FakeClock()
        self.overrides = dict(overrides or {})
        self.read_timeout_s = read_timeout_s
        self.written: list[str] = []
        self._outbox: list[tuple[float, int, bytes]] = []  # (time ready, order, data)
        self._seq = 0
        self._calls: dict[str, int] = {}
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
            raise OSError("fake: device disconnected")
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
            raise OSError("fake: device disconnected")
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

    def _replies_for(self, cmd: str) -> Sequence[Reply]:
        n = self._calls.get(cmd, 0)
        self._calls[cmd] = n + 1
        if cmd not in self.overrides:
            return default_replies(cmd)
        script = self.overrides[cmd]
        # A list of lists means "a different answer on each call".
        if script and isinstance(script[0], list):
            return script[n] if n < len(script) else default_replies(cmd)
        return script  # type: ignore[return-value]
