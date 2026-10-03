"""Full cycle on the Pi side with a simulated camera, model and Arduino (no hardware)."""

import pytest

from replast.config import decision_params, load_config, serial_commands
from replast.decision import Detection
from replast.logger import TransactionLog
from replast.main import Machine, MachineHalted
from replast.serial_link import SerialLink
from replast.simulator import SimClock, SimulatedArduino, SimulatedCamera, SimulatedDetector


def frames_of(cls, conf, n=5):
    return [[Detection(cls, conf)] for _ in range(n)]


def make(script, *, camera_fail=False, model_fail=False, overrides=None, clears=True):
    cfg = load_config()
    clock = SimClock()
    ard = SimulatedArduino(clock, overrides)
    link = SerialLink(ard, clock=clock)
    said = []

    class Log(TransactionLog):
        pass

    import tempfile, pathlib
    tlog = TransactionLog(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    saved = []
    m = Machine(
        link, SimulatedCamera(fail=camera_fail), SimulatedDetector(script, fail=model_fail),
        decision_params(cfg), serial_commands(cfg), tlog,
        image_saver=lambda frames, stem: saved.append(stem) or [f"{stem}_0.jpg"],
        clock=clock, say=said.append,
    )
    return m, ard, tlog, said, saved


def place_item(ard, clears=True):
    ard.push("OBJECT")
    if clears:
        ard.push("CLEAR", delay=2.0)   # item falls off when the platform tips


@pytest.mark.parametrize("cls, command, material", [
    ("PLASTIC", "SORT_PET", "PET"),
    ("METAL", "SORT_AL", "ALUMINIUM"),
    ("PAPER", "SORT_PAPER", "PAPER"),
])
def test_clean_item_is_sorted_and_logged(cls, command, material):
    m, ard, tlog, said, saved = make([frames_of(cls, 0.9)])
    place_item(ard)
    t = m.run_cycle(wait_s=1.0)
    assert ard.commands() == [command]
    row = tlog.rows()[0]
    assert row["material"] == material
    assert row["command"] == command
    assert row["outcome"] == "SORTED"
    assert row["reason_code"] == "OK"
    assert row["winning_class"] == cls
    assert row["frames_detected"] == 5 and row["frames_agreeing"] == 5
    assert row["mean_conf"] == pytest.approx(0.9)
    assert row["item_cleared"] == 1
    assert row["inference_ms"] == pytest.approx(100.0)
    assert row["response_time_s"] < 1.0
    assert row["cycle_time_s"] > row["response_time_s"]
    assert saved and saved[0].endswith(material)


def test_uncertain_item_goes_to_reject_with_reason():
    m, ard, tlog, said, _ = make([frames_of("PLASTIC", 0.5)])
    place_item(ard)
    m.run_cycle()
    assert ard.commands() == ["REJECT"]
    row = tlog.rows()[0]
    assert row["outcome"] == "REJECTED"
    assert row["reason_code"] == "LOW_CONF"


def test_other_goes_to_reject():
    m, ard, tlog, _, _ = make([frames_of("OTHER", 0.95)])
    place_item(ard)
    m.run_cycle()
    assert ard.commands() == ["REJECT"]
    assert tlog.rows()[0]["reason_code"] == "CLASS_OTHER"


def test_no_item_does_nothing():
    m, ard, tlog, _, _ = make([])
    assert m.run_cycle(wait_s=1.0) is None
    assert ard.commands() == []
    assert tlog.rows() == []


@pytest.mark.parametrize("camera_fail, model_fail", [(True, False), (False, True)])
def test_vision_failure_sends_item_to_reject_never_a_material_bin(camera_fail, model_fail):
    m, ard, tlog, said, _ = make([frames_of("PLASTIC", 0.99)], camera_fail=camera_fail, model_fail=model_fail)
    place_item(ard)
    m.run_cycle()
    assert ard.commands() == ["REJECT"]
    row = tlog.rows()[0]
    assert row["outcome"] == "ERROR"
    assert row["material"] == "REJECT"
    assert "vision" in row["error_msg"]


@pytest.mark.parametrize("overrides", [
    {"SORT_PET": []},                                   # no ACK
    {"SORT_PET": [(0.02, "ACK:SORT_PET")]},             # no DONE
    {"SORT_PET": [(0.02, "ERR:TIMEOUT")]},              # Arduino error
])
def test_mechanism_failure_stops_motors_logs_and_halts(overrides):
    m, ard, tlog, said, _ = make([frames_of("PLASTIC", 0.9)], overrides=overrides)
    place_item(ard)
    with pytest.raises(MachineHalted):
        m.run_cycle()
    assert ard.commands()[-1] == "STOP"
    row = tlog.rows()[0]
    assert row["outcome"] == "ERROR"
    assert "mechanism" in row["error_msg"]


def test_stuck_item_is_flagged():
    m, ard, tlog, said, _ = make([frames_of("PAPER", 0.9)])
    place_item(ard, clears=False)
    m.run_cycle()
    assert tlog.rows()[0]["item_cleared"] == 0
    assert any("stuck" in s for s in said)


def test_two_items_in_a_row():
    m, ard, tlog, _, _ = make([frames_of("PAPER", 0.9), frames_of("METAL", 0.9)])
    place_item(ard)
    m.run_cycle()
    place_item(ard)
    m.run_cycle()
    assert ard.commands() == ["SORT_PAPER", "SORT_AL"]
    assert [r["material"] for r in tlog.rows()] == ["PAPER", "ALUMINIUM"]


def test_never_sorts_into_material_bin_after_failed_rule_fuzz():
    """Random detections: whatever is sent, a material bin needs an OK decision."""
    import random
    rng = random.Random(7)
    classes = ["PLASTIC", "METAL", "PAPER", "OTHER"]
    script = [[[Detection(rng.choice(classes), round(rng.random(), 2)) for _ in range(rng.randint(0, 2))]
               for _ in range(5)] for _ in range(200)]
    m, ard, tlog, _, _ = make(list(script))
    for _ in range(200):
        place_item(ard)
        m.run_cycle()
    for row in tlog.rows():
        if row["command"] != "REJECT":
            assert row["reason_code"] == "OK"
