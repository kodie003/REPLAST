"""The REPLAST reject rule: turns 5 frames of detections into one verdict.

This module is deliberately PURE: no camera, no serial, no files. It only
takes plain data in and returns plain data out, so every rule can be unit
tested on a laptop.

The rule (project doc §6.3 / §9.4). REJECT if ANY of these is true, checked
in this order:

  1. LOW_DETECT_COUNT   item seen (conf >= det_conf) in fewer than min_detected frames
  2. CLASS_OTHER        the winning class maps to REJECT (the model's "OTHER")
     UNKNOWN_CLASS      the winning class is not in class_map at all
  3. LOW_AGREEMENT      fewer than min_agree frames voted for the winning class
  4. LOW_CONF           mean confidence of the agreeing frames < mean_conf
  5. MULTI_OBJECT       any frame has a box of a different class at >= second_obj_conf
  6. MASS_OUT_OF_RANGE  (only if mass_check is enabled) mass missing, < min_mass_g,
                        or outside the range for the winning class

Otherwise the verdict is the mapped class (PET / ALUMINIUM / PAPER).

Interpretation choices (documented because a reviewer will ask):
  * A frame's "vote" is its highest-confidence box at or above det_conf.
    Several boxes of the same class in one frame still count as one vote.
  * The winning class is the class with the most votes. A tie is broken by
    the higher summed confidence, then alphabetically, so the result is
    always the same for the same input. A tie can never reach the minimum
    agreement of 4/5 anyway, so ties always end in REJECT.
  * Rule 5 looks at EVERY box in EVERY frame (not just the votes): any box
    of a class other than the winner at >= second_obj_conf counts as a
    second item. This is the strict reading of the doc and errs towards
    rejecting rather than a wrong sort. It does not catch two items of the
    SAME class, which is fine: they would go to the same bin.
  * Boundaries are inclusive in the item's favour: exactly 4/5 detected,
    exactly 4/5 agreeing and a mean of exactly 0.70 all PASS; a different-
    class box at exactly 0.50 REJECTS; exactly min_mass_g passes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Sequence

# Floating point sums like 0.7 + 0.7 + 0.7 can land at 0.6999999999. Treat
# anything that close to the threshold as equal to it.
_EPS = 1e-9


class Verdict(str, Enum):
    PET = "PET"
    ALUMINIUM = "ALUMINIUM"
    PAPER = "PAPER"
    REJECT = "REJECT"


class Reason(str, Enum):
    OK = "OK"
    LOW_DETECT_COUNT = "LOW_DETECT_COUNT"
    CLASS_OTHER = "CLASS_OTHER"
    UNKNOWN_CLASS = "UNKNOWN_CLASS"
    LOW_AGREEMENT = "LOW_AGREEMENT"
    LOW_CONF = "LOW_CONF"
    MULTI_OBJECT = "MULTI_OBJECT"
    MASS_OUT_OF_RANGE = "MASS_OUT_OF_RANGE"


@dataclass(frozen=True)
class Detection:
    """One bounding box from the model. Only class and confidence matter here."""

    cls: str
    conf: float


Frame = Sequence[Detection]


@dataclass(frozen=True)
class DecisionParams:
    class_map: Mapping[str, Verdict]
    n_frames: int = 5
    det_conf: float = 0.25
    min_detected: int = 4
    min_agree: int = 4
    mean_conf: float = 0.70
    second_obj_conf: float = 0.50
    mass_check_enabled: bool = False
    min_mass_g: float = 2.0
    mass_ranges_g: Mapping[Verdict, tuple[float, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalise class names so "Plastic" and "PLASTIC" mean the same thing.
        object.__setattr__(
            self, "class_map", {k.upper(): Verdict(v) for k, v in self.class_map.items()}
        )


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: Reason                    # first failing rule, or OK
    reasons: tuple[Reason, ...]       # every failing rule, in rule order
    winning_class: Optional[str]      # model class name, None if nothing detected
    mean_conf: float                  # mean conf of agreeing frames (0.0 if none)
    frames_detected: int
    frames_agreeing: int


def frame_vote(frame: Frame, det_conf: float) -> Optional[Detection]:
    """Highest-confidence box at or above det_conf, or None if nothing qualifies."""
    kept = [d for d in frame if d.conf + _EPS >= det_conf]
    if not kept:
        return None
    return max(kept, key=lambda d: d.conf)


def decide(
    frames: Sequence[Frame],
    params: DecisionParams,
    mass_g: Optional[float] = None,
) -> Decision:
    """Apply the reject rule to one item. Never raises for odd detections;
    raises ValueError only if the caller passed the wrong number of frames."""
    if len(frames) != params.n_frames:
        raise ValueError(f"expected {params.n_frames} frames, got {len(frames)}")

    votes = [frame_vote(f, params.det_conf) for f in frames]
    votes = [Detection(v.cls.upper(), v.conf) for v in votes if v is not None]
    frames_detected = len(votes)

    winner, agreeing = _winning_class(votes)
    mean_conf = sum(v.conf for v in agreeing) / len(agreeing) if agreeing else 0.0

    reasons: list[Reason] = []

    # Rule 1
    if frames_detected < params.min_detected:
        reasons.append(Reason.LOW_DETECT_COUNT)

    if winner is not None:
        mapped = params.class_map.get(winner)

        # Rule 2
        if mapped is None:
            reasons.append(Reason.UNKNOWN_CLASS)
        elif mapped is Verdict.REJECT:
            reasons.append(Reason.CLASS_OTHER)

        # Rule 3
        if len(agreeing) < params.min_agree:
            reasons.append(Reason.LOW_AGREEMENT)

        # Rule 4
        if mean_conf + _EPS < params.mean_conf:
            reasons.append(Reason.LOW_CONF)

        # Rule 5
        if _has_second_object(frames, winner, params.second_obj_conf):
            reasons.append(Reason.MULTI_OBJECT)

        # Rule 6
        if params.mass_check_enabled and mapped not in (None, Verdict.REJECT):
            if not _mass_ok(mass_g, mapped, params):
                reasons.append(Reason.MASS_OUT_OF_RANGE)

    if reasons:
        verdict = Verdict.REJECT
    else:
        verdict = params.class_map[winner]  # type: ignore[index]  # winner is set when no reasons

    return Decision(
        verdict=verdict,
        reason=reasons[0] if reasons else Reason.OK,
        reasons=tuple(reasons) if reasons else (Reason.OK,),
        winning_class=winner,
        mean_conf=mean_conf,
        frames_detected=frames_detected,
        frames_agreeing=len(agreeing),
    )


def _winning_class(votes: list[Detection]) -> tuple[Optional[str], list[Detection]]:
    """Most-voted class and the votes that agree with it."""
    if not votes:
        return None, []
    counts = Counter(v.cls for v in votes)
    conf_sum = Counter()
    for v in votes:
        conf_sum[v.cls] += v.conf
    # Sort key: most votes, then highest total confidence, then name (stable).
    winner = min(counts, key=lambda c: (-counts[c], -conf_sum[c], c))
    return winner, [v for v in votes if v.cls == winner]


def _has_second_object(frames: Sequence[Frame], winner: str, threshold: float) -> bool:
    return any(
        d.cls.upper() != winner and d.conf + _EPS >= threshold
        for frame in frames
        for d in frame
    )


def _mass_ok(mass_g: Optional[float], verdict: Verdict, params: DecisionParams) -> bool:
    if mass_g is None:
        return False  # mass check is on but we have no reading: do not guess
    if mass_g + _EPS < params.min_mass_g:
        return False
    rng = params.mass_ranges_g.get(verdict)
    if rng is None:
        return False  # no calibrated range for this class: reject rather than guess
    lo, hi = rng
    return lo - _EPS <= mass_g <= hi + _EPS
