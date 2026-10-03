"""Talks to the Arduino over USB serial using the protocol in docs/PROTOCOL.md.

The port is passed in from outside, so tests can hand over a simulated Arduino
(replast/simulator.py) instead of a real one. Only open_serial() touches pyserial.

Safety rule built in here, at the lowest level: if a movement command fails
in ANY way (no ACK, no DONE, ERR, unplugged cable), the link sends STOP
before raising, so the Arduino refuses further movement until a RESET.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

log = logging.getLogger(__name__)

MOVE_COMMANDS = frozenset({"SORT_PET", "SORT_PAPER", "SORT_AL", "REJECT", "RESET"})


class SerialPort(Protocol):
    """The three methods we need from pyserial's Serial (or the simulator)."""

    def write(self, data: bytes) -> Optional[int]: ...
    def readline(self) -> bytes: ...  # returns b"" when its short read timeout expires
    def close(self) -> None: ...


class SerialLinkError(Exception):
    """Base class: anything that went wrong talking to the Arduino."""


class LinkDeadError(SerialLinkError):
    """No answer to PING, or the port itself failed (e.g. cable pulled)."""


class AckTimeout(SerialLinkError):
    pass


class DoneTimeout(SerialLinkError):
    pass


class ArduinoError(SerialLinkError):
    def __init__(self, code: str, command: str = ""):
        self.code = code
        self.command = command
        super().__init__(f"Arduino reported ERR:{code}" + (f" during {command}" if command else ""))


class StoppedError(SerialLinkError):
    """The Arduino reported STOPPED while we were waiting on a command."""


@dataclass(frozen=True)
class CommandResult:
    command: str
    ack_s: float   # seconds from sending to ACK
    done_s: float  # seconds from sending to DONE


class SerialLink:
    def __init__(
        self,
        port: SerialPort,
        *,
        ack_timeout_s: float = 1.0,
        done_timeout_s: float = 15.0,
        ping_timeout_s: float = 1.0,
        ping_retries: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.port = port
        self.ack_timeout_s = ack_timeout_s
        self.done_timeout_s = done_timeout_s
        self.ping_timeout_s = ping_timeout_s
        self.ping_retries = ping_retries
        self.clock = clock
        # Set by OBJECT, cleared by CLEAR. OBJECT can arrive while we are busy
        # waiting for something else, so remember it instead of dropping it.
        self._object_pending = False
        self.object_present = False
        self.ignored_lines: list[str] = []

    # ---- low level -------------------------------------------------------

    def send_line(self, line: str) -> None:
        log.debug("PI  -> %s", line)
        try:
            self.port.write((line + "\n").encode("ascii"))
        except (OSError, ValueError) as e:  # pyserial errors subclass OSError
            raise LinkDeadError(f"could not write to serial port: {e}") from e

    def _read_one(self) -> Optional[str]:
        """One line from the port, or None if nothing arrived in the port's short timeout."""
        try:
            raw = self.port.readline()
        except (OSError, ValueError) as e:
            raise LinkDeadError(f"could not read serial port: {e}") from e
        if not raw:
            return None
        line = raw.decode("ascii", errors="replace").strip()
        log.debug("ARD -> %s", line)
        return line or None

    def _wait_for(self, wanted: Callable[[str], bool], timeout_s: float, command: str = "") -> Optional[str]:
        """Read lines until one satisfies `wanted`, or time runs out (returns None).

        Along the way: OBJECT/CLEAR are remembered, ERR raises ArduinoError,
        STOPPED raises StoppedError, anything else is ignored and recorded.
        """
        deadline = self.clock() + timeout_s
        while self.clock() < deadline:
            line = self._read_one()
            if line is None:
                continue
            if wanted(line):
                return line
            if line == "OBJECT":
                self._object_pending = True
                self.object_present = True
            elif line == "CLEAR":
                self._object_pending = False
                self.object_present = False
            elif line.startswith("ERR:"):
                raise ArduinoError(line[4:], command)
            elif line == "STOPPED":
                raise StoppedError(f"Arduino stopped during {command or 'wait'}")
            elif not line.startswith("#"):
                self.ignored_lines.append(line)
                log.info("ignored unexpected line from Arduino: %r", line)
        return None

    # ---- public API ------------------------------------------------------

    def wait_ready(self, timeout_s: float = 4.0) -> bool:
        """After opening the port the Arduino may restart; wait for its READY."""
        return self._wait_for(lambda l: l == "READY", timeout_s) is not None

    def ping(self) -> bool:
        """True if the Arduino answers PONG within ping_retries attempts."""
        for attempt in range(1, self.ping_retries + 1):
            self.send_line("PING")
            try:
                if self._wait_for(lambda l: l == "PONG", self.ping_timeout_s) is not None:
                    return True
            except ArduinoError:
                pass  # an old error line is not an answer to PING; keep trying
            log.warning("no PONG (attempt %d/%d)", attempt, self.ping_retries)
        return False

    def connect(self, ready_timeout_s: float = 4.0) -> None:
        """Wait for READY (if the board restarted) then confirm with PING. Raises LinkDeadError."""
        self.wait_ready(ready_timeout_s)  # fine if it doesn't come: board may not have restarted
        if not self.ping():
            raise LinkDeadError(f"Arduino did not answer PING after {self.ping_retries} tries")

    def run_command(self, command: str) -> CommandResult:
        """Send a movement command and wait for ACK then DONE.

        On ANY failure, sends STOP (best effort) and re-raises, so nothing keeps moving.
        """
        if command not in MOVE_COMMANDS:
            raise ValueError(f"not a movement command: {command!r}")

        t0 = self.clock()
        try:
            self.send_line(command)
            if self._wait_for(lambda l: l == f"ACK:{command}", self.ack_timeout_s, command) is None:
                raise AckTimeout(f"no ACK for {command} within {self.ack_timeout_s}s")
            t_ack = self.clock() - t0
            if self._wait_for(lambda l: l == f"DONE:{command}", self.done_timeout_s, command) is None:
                raise DoneTimeout(f"no DONE for {command} within {self.done_timeout_s}s")
            t_done = self.clock() - t0
        except SerialLinkError:
            self.stop()
            raise
        return CommandResult(command, t_ack, t_done)

    def stop(self, timeout_s: float = 1.0) -> bool:
        """Send STOP and wait briefly for STOPPED. Never raises: used on error paths."""
        try:
            self.send_line("STOP")
            return self._wait_for(lambda l: l == "STOPPED", timeout_s, "STOP") is not None
        except SerialLinkError as e:
            log.error("STOP could not be confirmed: %s", e)
            return False

    def read_reply(self, timeout_s: float) -> Optional[str]:
        """Next reply line of any kind (including ERR:/STOPPED, without raising).
        OBJECT/CLEAR and # comments are skipped. Used by the hardware test scripts."""
        deadline = self.clock() + timeout_s
        while self.clock() < deadline:
            line = self._read_one()
            if line is None or line.startswith("#"):
                continue
            if line in ("OBJECT", "CLEAR"):
                self._object_pending = line == "OBJECT"
                self.object_present = line == "OBJECT"
                continue
            return line
        return None

    def wait_for_object(self, timeout_s: float) -> bool:
        """True as soon as the Arduino reports OBJECT (including one that arrived earlier)."""
        if self._object_pending:
            self._object_pending = False
            return True
        if self._wait_for(lambda l: l == "OBJECT", timeout_s) is not None:
            self.object_present = True
            return True
        return False

    def clear_pending_object(self) -> None:
        """Forget an OBJECT seen earlier (e.g. one that arrived during the last sort)."""
        self._object_pending = False

    def close(self) -> None:
        try:
            self.port.close()
        except Exception:  # closing is best effort
            pass


def open_serial(port: str, baud: int, read_timeout_s: float = 0.1) -> SerialPort:
    """Open the real port. Imported lazily so tests don't need pyserial installed."""
    import serial  # pyserial

    return serial.Serial(port, baud, timeout=read_timeout_s, write_timeout=1.0)


def link_from_config(cfg: dict, port: Optional[SerialPort] = None) -> SerialLink:
    s = cfg["serial"]
    if port is None:
        port = open_serial(s["port"], int(s["baud"]), float(s.get("read_timeout_s", 0.1)))
    return SerialLink(
        port,
        ack_timeout_s=float(s["ack_timeout_s"]),
        done_timeout_s=float(s["done_timeout_s"]),
        ping_timeout_s=float(s["ping_timeout_s"]),
        ping_retries=int(s["ping_retries"]),
    )
