"""Tests for replast/serial_link.py using the FakeArduino (no hardware)."""

import pytest

from replast.config import load_config
from replast.fakes import FakeArduino, FakeClock
from replast.serial_link import (
    AckTimeout,
    ArduinoError,
    DoneTimeout,
    LinkDeadError,
    SerialLink,
    StoppedError,
    link_from_config,
)


def make(overrides=None):
    clock = FakeClock()
    ard = FakeArduino(clock, overrides)
    link = SerialLink(ard, ack_timeout_s=1.0, done_timeout_s=15.0, ping_timeout_s=1.0,
                      ping_retries=3, clock=clock)
    return link, ard, clock


# --- PING / connect ----------------------------------------------------------

def test_ping_happy():
    link, ard, _ = make()
    assert link.ping() is True
    assert ard.commands() == ["PING"]


def test_ping_retries_after_a_lost_reply():
    link, ard, _ = make({"PING": [[], [(0.01, "PONG")]]})
    assert link.ping() is True
    assert ard.commands() == ["PING", "PING"]


def test_ping_gives_up_after_retries():
    link, ard, clock = make({"PING": []})
    assert link.ping() is False
    assert ard.commands() == ["PING"] * 3
    assert clock() == pytest.approx(3.0, abs=0.2)


def test_connect_waits_for_ready_then_pings():
    link, ard, _ = make()
    ard.push("READY", delay=1.5)
    link.connect(ready_timeout_s=4.0)
    assert ard.commands() == ["PING"]


def test_connect_works_without_ready():
    # If opening the port did not restart the board, READY never comes. That's fine.
    link, ard, _ = make()
    link.connect(ready_timeout_s=0.5)


def test_connect_raises_when_arduino_silent():
    link, _, _ = make({"PING": []})
    with pytest.raises(LinkDeadError):
        link.connect(ready_timeout_s=0.2)


def test_ping_ignores_stale_error_line():
    link, ard, _ = make({"PING": [[(0.01, "ERR:BUSY")], [(0.01, "PONG")]]})
    assert link.ping() is True


# --- movement commands -------------------------------------------------------

@pytest.mark.parametrize("cmd", ["SORT_PET", "SORT_PAPER", "SORT_AL", "REJECT", "RESET"])
def test_command_happy_path(cmd):
    link, ard, _ = make()
    r = link.run_command(cmd)
    assert ard.commands() == [cmd]  # no STOP on success
    assert r.command == cmd
    assert r.ack_s == pytest.approx(0.02, abs=0.01)
    assert r.done_s == pytest.approx(3.0, abs=0.01)


def test_unknown_command_is_refused_before_sending():
    link, ard, _ = make()
    with pytest.raises(ValueError):
        link.run_command("SORT_GLASS")
    with pytest.raises(ValueError):
        link.run_command("PING")
    assert ard.commands() == []


def test_missing_ack_times_out_and_stops():
    link, ard, clock = make({"SORT_PET": []})
    with pytest.raises(AckTimeout):
        link.run_command("SORT_PET")
    assert ard.commands() == ["SORT_PET", "STOP"]
    assert clock() == pytest.approx(1.0, abs=0.2)


def test_missing_done_times_out_and_stops():
    link, ard, clock = make({"SORT_AL": [(0.02, "ACK:SORT_AL")]})
    with pytest.raises(DoneTimeout):
        link.run_command("SORT_AL")
    assert ard.commands() == ["SORT_AL", "STOP"]
    assert clock() == pytest.approx(15.0, abs=0.3)


def test_done_just_inside_timeout_is_accepted():
    link, _, _ = make({"REJECT": [(0.02, "ACK:REJECT"), (14.8, "DONE:REJECT")]})
    assert link.run_command("REJECT").done_s == pytest.approx(14.8, abs=0.01)


@pytest.mark.parametrize("code, when", [("BUSY", 0.01), ("TIMEOUT", 2.0), ("STOPPED", 0.01)])
def test_err_reply_raises_and_stops(code, when):
    replies = [(when, f"ERR:{code}")] if when < 0.02 else [(0.02, "ACK:SORT_PAPER"), (when, f"ERR:{code}")]
    link, ard, _ = make({"SORT_PAPER": replies})
    with pytest.raises(ArduinoError) as e:
        link.run_command("SORT_PAPER")
    assert e.value.code == code
    assert e.value.command == "SORT_PAPER"
    assert ard.commands()[-1] == "STOP"


def test_stopped_mid_cycle_raises():
    link, ard, _ = make({"SORT_PAPER": [(0.02, "ACK:SORT_PAPER"), (1.0, "STOPPED")]})
    with pytest.raises(StoppedError):
        link.run_command("SORT_PAPER")
    assert ard.commands()[-1] == "STOP"


def test_garbage_lines_are_ignored():
    link, ard, _ = make({
        "SORT_PET": [
            (0.01, "#debug: moving"),
            (0.015, "ACK:SORT_AL"),         # stale ACK for another command
            (0.02, "ACK:SORT_PET"),
            (0.5, "hello?"),
            (1.0, "DONE:SORT_AL"),          # stale DONE for another command
            (3.0, "DONE:SORT_PET"),
        ]
    })
    ard.push(b"\xff\xfe\x00garbage\n")
    ard.push(b"\n")
    link.run_command("SORT_PET")
    assert ard.commands() == ["SORT_PET"]
    assert "hello?" in link.ignored_lines
    assert "ACK:SORT_AL" in link.ignored_lines
    assert not any(l.startswith("#") for l in link.ignored_lines)


def test_windows_line_endings_are_accepted():
    link, ard, _ = make({"SORT_PET": []})
    ard.push(b"ACK:SORT_PET\r\n", 0.01)
    ard.push(b"DONE:SORT_PET\r\n", 0.5)
    assert link.run_command("SORT_PET").done_s == pytest.approx(0.5, abs=0.01)


def test_cable_pulled_on_write():
    link, ard, _ = make()
    ard.fail_writes = True
    with pytest.raises(LinkDeadError):
        link.run_command("SORT_PET")


def test_cable_pulled_on_read():
    link, ard, _ = make()
    ard.fail_reads = True
    with pytest.raises(LinkDeadError):
        link.run_command("SORT_PET")


# --- STOP -----------------------------------------------------------------

def test_stop_confirmed():
    link, ard, _ = make()
    assert link.stop() is True
    assert ard.commands() == ["STOP"]


def test_stop_never_raises():
    link, ard, _ = make()
    ard.fail_writes = True
    assert link.stop() is False
    link, ard, _ = make({"STOP": []})
    assert link.stop() is False


# --- OBJECT / CLEAR ----------------------------------------------------------

def test_wait_for_object():
    link, ard, _ = make()
    ard.push("OBJECT", delay=2.0)
    assert link.wait_for_object(timeout_s=5.0) is True
    assert link.object_present is True


def test_wait_for_object_times_out():
    link, _, clock = make()
    assert link.wait_for_object(timeout_s=1.0) is False
    assert clock() == pytest.approx(1.0, abs=0.2)


def test_object_seen_during_other_wait_is_not_lost():
    link, ard, _ = make()
    ard.push("OBJECT")
    assert link.ping() is True
    assert link.wait_for_object(timeout_s=0.0) is True
    # ...and is only reported once
    assert link.wait_for_object(timeout_s=0.5) is False


def test_clear_cancels_pending_object():
    link, ard, _ = make()
    ard.push("OBJECT")
    ard.push("CLEAR")
    link.ping()
    assert link.object_present is False
    assert link.wait_for_object(timeout_s=0.5) is False


def test_clear_pending_object():
    link, ard, _ = make()
    ard.push("OBJECT")
    link.ping()
    link.clear_pending_object()
    assert link.wait_for_object(timeout_s=0.5) is False


# --- config -----------------------------------------------------------------

def test_link_from_config_uses_config_values():
    link = link_from_config(load_config(), port=FakeArduino())
    assert link.ack_timeout_s == 1.0
    assert link.done_timeout_s == 15.0
    assert link.ping_retries == 3


def test_close():
    link, ard, _ = make()
    link.close()
    assert ard.closed
