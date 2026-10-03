"""Unit tests for the reject rule (replast/decision.py).

Frames are built by hand with the helper `f()`:
    f(("PLASTIC", 0.9))                  -> one frame with one box
    f(("PLASTIC", 0.9), ("PAPER", 0.6))  -> one frame with two boxes
    f()                                  -> an empty frame (nothing detected)
"""

import pytest

from replast.decision import (
    Decision,
    DecisionParams,
    Detection,
    Reason,
    Verdict,
    decide,
    frame_vote,
)

CLASS_MAP = {"PLASTIC": "PET", "METAL": "ALUMINIUM", "PAPER": "PAPER", "OTHER": "REJECT"}


def f(*boxes):
    return [Detection(c, conf) for c, conf in boxes]


def same(cls, conf, n=5):
    """n identical frames, each with one box."""
    return [f((cls, conf)) for _ in range(n)]


@pytest.fixture
def params():
    return DecisionParams(class_map=CLASS_MAP)


@pytest.fixture
def mass_params():
    return DecisionParams(
        class_map=CLASS_MAP,
        mass_check_enabled=True,
        min_mass_g=2.0,
        mass_ranges_g={
            Verdict.PET: (10.0, 50.0),
            Verdict.ALUMINIUM: (10.0, 20.0),
            Verdict.PAPER: (2.0, 30.0),
        },
    )


# --- happy path: every sortable class reaches its bin -----------------------

@pytest.mark.parametrize(
    "cls, verdict",
    [("PLASTIC", Verdict.PET), ("METAL", Verdict.ALUMINIUM), ("PAPER", Verdict.PAPER)],
)
def test_clean_item_is_sorted(params, cls, verdict):
    d = decide(same(cls, 0.9), params)
    assert d.verdict is verdict
    assert d.reason is Reason.OK
    assert d.reasons == (Reason.OK,)
    assert d.winning_class == cls
    assert d.frames_detected == 5
    assert d.frames_agreeing == 5
    assert d.mean_conf == pytest.approx(0.9)


def test_class_names_are_case_insensitive(params):
    assert decide(same("Plastic", 0.9), params).verdict is Verdict.PET


# --- rule 1: detected in fewer than 4 of 5 frames ---------------------------

@pytest.mark.parametrize(
    "n_seen, verdict",
    [(5, Verdict.PET), (4, Verdict.PET), (3, Verdict.REJECT), (1, Verdict.REJECT), (0, Verdict.REJECT)],
)
def test_detect_count_boundary(params, n_seen, verdict):
    frames = same("PLASTIC", 0.9, n_seen) + [f()] * (5 - n_seen)
    d = decide(frames, params)
    assert d.verdict is verdict
    assert d.frames_detected == n_seen
    if verdict is Verdict.REJECT:
        assert d.reason is Reason.LOW_DETECT_COUNT


def test_all_frames_empty(params):
    d = decide([f()] * 5, params)
    assert d.verdict is Verdict.REJECT
    assert d.reasons == (Reason.LOW_DETECT_COUNT,)
    assert d.winning_class is None
    assert d.mean_conf == 0.0


def test_boxes_below_det_conf_do_not_count(params):
    frames = same("PLASTIC", 0.9, 3) + [f(("PLASTIC", 0.24))] * 2
    d = decide(frames, params)
    assert d.frames_detected == 3
    assert d.reason is Reason.LOW_DETECT_COUNT


def test_box_exactly_at_det_conf_counts(params):
    # 0.25 is detected; but mean conf is then low, so LOW_CONF (not LOW_DETECT_COUNT)
    frames = same("PLASTIC", 0.9, 3) + [f(("PLASTIC", 0.25))] * 2
    d = decide(frames, params)
    assert d.frames_detected == 5
    assert Reason.LOW_DETECT_COUNT not in d.reasons


# --- rule 2: winning class is OTHER / unknown --------------------------------

def test_all_other_is_rejected(params):
    d = decide(same("OTHER", 0.95), params)
    assert d.verdict is Verdict.REJECT
    assert d.reasons == (Reason.CLASS_OTHER,)
    assert d.winning_class == "OTHER"


def test_unknown_class_is_rejected(params):
    d = decide(same("GLASS", 0.95), params)
    assert d.verdict is Verdict.REJECT
    assert d.reason is Reason.UNKNOWN_CLASS


@pytest.mark.parametrize("cls", ["PLASTIC", "METAL", "PAPER", "OTHER", "plastic", "GLASS", ""])
def test_class_mapping_table(params, cls):
    expected = {
        "PLASTIC": Verdict.PET,
        "METAL": Verdict.ALUMINIUM,
        "PAPER": Verdict.PAPER,
    }.get(cls.upper(), Verdict.REJECT)
    assert decide(same(cls, 0.9), params).verdict is expected


# --- rule 3: fewer than 4 of 5 frames agree ---------------------------------

def test_four_of_five_agree_passes(params):
    # The odd frame is a low-confidence different class (< 0.50, so rule 5 is not hit)
    frames = same("PLASTIC", 0.9, 4) + [f(("PAPER", 0.4))]
    d = decide(frames, params)
    assert d.verdict is Verdict.PET
    assert d.frames_agreeing == 4


def test_three_of_five_agree_rejects(params):
    frames = same("PLASTIC", 0.9, 3) + [f(("PAPER", 0.4))] * 2
    d = decide(frames, params)
    assert d.verdict is Verdict.REJECT
    assert d.reason is Reason.LOW_AGREEMENT
    assert d.frames_agreeing == 3


def test_tie_2_2_1_is_rejected(params):
    frames = same("PLASTIC", 0.45, 2) + same("PAPER", 0.45, 2) + [f(("METAL", 0.45))]
    d = decide(frames, params)
    assert d.verdict is Verdict.REJECT
    assert d.reason is Reason.LOW_AGREEMENT
    assert d.frames_agreeing == 2


def test_tie_is_deterministic(params):
    # Equal counts -> higher total confidence wins the tie (for logging)
    frames = same("PLASTIC", 0.4, 2) + same("PAPER", 0.45, 2) + [f()]
    assert decide(frames, params).winning_class == "PAPER"
    # Equal counts and equal confidence -> alphabetical
    frames = same("PLASTIC", 0.4, 2) + same("PAPER", 0.4, 2) + [f()]
    assert decide(frames, params).winning_class == "PAPER"


def test_tie_between_other_and_plastic_rejects(params):
    frames = same("OTHER", 0.45, 2) + same("PLASTIC", 0.4, 2) + [f()]
    d = decide(frames, params)
    assert d.verdict is Verdict.REJECT
    assert d.reasons[0] is Reason.CLASS_OTHER


# --- rule 4: mean confidence of agreeing frames ------------------------------

@pytest.mark.parametrize(
    "conf, verdict",
    [(0.70, Verdict.PET), (0.71, Verdict.PET), (0.69, Verdict.REJECT), (0.30, Verdict.REJECT)],
)
def test_mean_conf_boundary(params, conf, verdict):
    d = decide(same("PLASTIC", conf), params)
    assert d.verdict is verdict
    if verdict is Verdict.REJECT:
        assert d.reasons == (Reason.LOW_CONF,)


def test_mean_conf_exactly_070_from_mixed_values(params):
    # 0.6, 0.7, 0.8, 0.7 -> mean 0.70 exactly (floating point must not reject it)
    frames = [f(("PLASTIC", c)) for c in (0.6, 0.7, 0.8, 0.7)] + [f()]
    d = decide(frames, params)
    assert d.mean_conf == pytest.approx(0.70)
    assert d.verdict is Verdict.PET


def test_mean_conf_uses_only_agreeing_frames(params):
    # A confident disagreeing frame must not pull the mean up
    frames = same("PLASTIC", 0.6, 4) + [f(("PAPER", 0.45))]
    d = decide(frames, params)
    assert d.mean_conf == pytest.approx(0.6)
    assert d.reason is Reason.LOW_CONF


def test_frame_vote_is_highest_confidence_box():
    v = frame_vote(f(("PLASTIC", 0.5), ("PLASTIC", 0.9), ("PAPER", 0.3)), det_conf=0.25)
    assert v == Detection("PLASTIC", 0.9)
    assert frame_vote(f(("PLASTIC", 0.1)), det_conf=0.25) is None
    assert frame_vote(f(), det_conf=0.25) is None


def test_duplicate_boxes_same_class_count_once(params):
    # Two plastic boxes in each frame (e.g. bottle + its cap) is still one item
    frames = [f(("PLASTIC", 0.9), ("PLASTIC", 0.6)) for _ in range(5)]
    d = decide(frames, params)
    assert d.verdict is Verdict.PET
    assert d.mean_conf == pytest.approx(0.9)


# --- rule 5: second item of a different class -------------------------------

@pytest.mark.parametrize(
    "second_conf, verdict",
    [(0.49, Verdict.PET), (0.50, Verdict.REJECT), (0.80, Verdict.REJECT)],
)
def test_second_object_boundary(params, second_conf, verdict):
    frames = same("PLASTIC", 0.9, 4) + [f(("PLASTIC", 0.9), ("METAL", second_conf))]
    d = decide(frames, params)
    assert d.verdict is verdict
    if verdict is Verdict.REJECT:
        assert d.reasons == (Reason.MULTI_OBJECT,)


def test_second_object_in_disagreeing_frame_rejects(params):
    # Strict reading: a confident different-class box anywhere is a second item
    frames = same("PLASTIC", 0.9, 4) + [f(("PAPER", 0.6))]
    d = decide(frames, params)
    assert d.verdict is Verdict.REJECT
    assert d.reason is Reason.MULTI_OBJECT


def test_second_object_of_same_class_is_allowed(params):
    frames = [f(("PLASTIC", 0.9), ("PLASTIC", 0.8)) for _ in range(5)]
    assert decide(frames, params).verdict is Verdict.PET


# --- rule 6: mass ------------------------------------------------------------

def test_mass_ignored_when_disabled(params):
    # No load cell: mass is None and must not cause a reject
    assert decide(same("PLASTIC", 0.9), params, mass_g=None).verdict is Verdict.PET
    assert decide(same("PLASTIC", 0.9), params, mass_g=0.0).verdict is Verdict.PET


@pytest.mark.parametrize(
    "cls, mass, verdict",
    [
        ("PLASTIC", 25.0, Verdict.PET),
        ("PLASTIC", 10.0, Verdict.PET),        # range edge is inclusive
        ("PLASTIC", 50.0, Verdict.PET),
        ("PLASTIC", 9.9, Verdict.REJECT),
        ("PLASTIC", 50.1, Verdict.REJECT),
        ("PAPER", 2.0, Verdict.PAPER),         # exactly 2 g passes
        ("PAPER", 1.99, Verdict.REJECT),       # below 2 g always rejects
        ("METAL", 15.0, Verdict.ALUMINIUM),
        ("METAL", 40.0, Verdict.REJECT),
        ("PLASTIC", None, Verdict.REJECT),     # check enabled but no reading
    ],
)
def test_mass_rule(mass_params, cls, mass, verdict):
    d = decide(same(cls, 0.9), mass_params, mass_g=mass)
    assert d.verdict is verdict
    if verdict is Verdict.REJECT:
        assert d.reasons == (Reason.MASS_OUT_OF_RANGE,)


def test_below_min_mass_rejects_even_if_range_allows_it():
    p = DecisionParams(
        class_map=CLASS_MAP,
        mass_check_enabled=True,
        min_mass_g=2.0,
        mass_ranges_g={Verdict.PAPER: (0.0, 30.0)},
    )
    assert decide(same("PAPER", 0.9), p, mass_g=1.5).reason is Reason.MASS_OUT_OF_RANGE


def test_mass_without_range_for_class_rejects():
    p = DecisionParams(class_map=CLASS_MAP, mass_check_enabled=True, mass_ranges_g={})
    assert decide(same("PAPER", 0.9), p, mass_g=10.0).reason is Reason.MASS_OUT_OF_RANGE


# --- several reasons at once: first in rule order is reported ---------------

def test_multiple_reasons_are_all_listed_in_order(params):
    # 2 low-confidence OTHER frames, 1 confident PAPER frame, 2 empty frames:
    # rule 1 (3/5 seen), rule 2 (OTHER wins 2-1), rule 3 (2 agree),
    # rule 4 (mean 0.4), rule 5 (PAPER box at 0.6)
    frames = same("OTHER", 0.4, 2) + [f(("PAPER", 0.6))] + [f(), f()]
    d = decide(frames, params)
    assert d.verdict is Verdict.REJECT
    assert d.reasons == (
        Reason.LOW_DETECT_COUNT,
        Reason.CLASS_OTHER,
        Reason.LOW_AGREEMENT,
        Reason.LOW_CONF,
        Reason.MULTI_OBJECT,
    )
    assert d.reason is Reason.LOW_DETECT_COUNT


# --- input checks and configurability ---------------------------------------

@pytest.mark.parametrize("n", [0, 4, 6])
def test_wrong_number_of_frames_raises(params, n):
    with pytest.raises(ValueError):
        decide(same("PLASTIC", 0.9, n), params)


def test_thresholds_are_configurable():
    p = DecisionParams(class_map=CLASS_MAP, n_frames=3, min_detected=2, min_agree=2, mean_conf=0.5)
    frames = same("METAL", 0.55, 2) + [f()]
    assert decide(frames, p).verdict is Verdict.ALUMINIUM


def test_decision_is_immutable(params):
    d = decide(same("PLASTIC", 0.9), params)
    assert isinstance(d, Decision)
    with pytest.raises(AttributeError):
        d.verdict = Verdict.PAPER  # type: ignore[misc]


# --- the safety property, checked over many random inputs -------------------

def test_never_sorts_unless_every_rule_passes(params):
    """Fuzz: whatever the detections, a non-REJECT verdict must satisfy every rule."""
    import random

    rng = random.Random(1234)
    classes = ["PLASTIC", "METAL", "PAPER", "OTHER", "GLASS"]
    for _ in range(5000):
        frames = [
            [Detection(rng.choice(classes), round(rng.random(), 2)) for _ in range(rng.randint(0, 3))]
            for _ in range(5)
        ]
        d = decide(frames, params)
        if d.verdict is Verdict.REJECT:
            assert d.reason is not Reason.OK
            continue
        assert d.reasons == (Reason.OK,)
        assert d.frames_detected >= 4
        assert d.frames_agreeing >= 4
        assert d.mean_conf >= 0.70 - 1e-9
        assert d.winning_class in ("PLASTIC", "METAL", "PAPER")
        assert not any(
            b.cls != d.winning_class and b.conf >= 0.50 for fr in frames for b in fr
        )
