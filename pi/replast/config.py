"""Load pi/config.yaml and turn it into checked, typed settings.

Bad config should fail loudly at start-up (before anything moves), not
halfway through a sort.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union

import yaml

from replast.decision import DecisionParams, Verdict

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class ConfigError(ValueError):
    pass


def load_config(path: Union[str, Path] = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path} is empty or not a YAML mapping")
    return cfg


def decision_params(cfg: dict[str, Any]) -> DecisionParams:
    try:
        d = cfg["decision"]
    except KeyError:
        raise ConfigError("config has no 'decision' section") from None

    try:
        class_map = {str(k): Verdict(str(v).upper()) for k, v in d["class_map"].items()}
    except ValueError as e:
        raise ConfigError(f"decision.class_map has an unknown outcome: {e}") from None

    mass = d.get("mass_check", {}) or {}
    try:
        ranges = {
            Verdict(str(k).upper()): (float(lo), float(hi))
            for k, (lo, hi) in (mass.get("ranges_g") or {}).items()
        }
    except (ValueError, TypeError) as e:
        raise ConfigError(f"decision.mass_check.ranges_g is invalid: {e}") from None

    params = DecisionParams(
        class_map=class_map,
        n_frames=int(d["n_frames"]),
        det_conf=float(d["det_conf"]),
        min_detected=int(d["min_detected"]),
        min_agree=int(d["min_agree"]),
        mean_conf=float(d["mean_conf"]),
        second_obj_conf=float(d["second_obj_conf"]),
        mass_check_enabled=bool(mass.get("enabled", False)),
        min_mass_g=float(mass.get("min_mass_g", 2.0)),
        mass_ranges_g=ranges,
    )
    _validate(params)
    return params


def _validate(p: DecisionParams) -> None:
    problems = []
    if p.n_frames < 1:
        problems.append("n_frames must be >= 1")
    for name in ("det_conf", "mean_conf", "second_obj_conf"):
        v = getattr(p, name)
        if not 0.0 <= v <= 1.0:
            problems.append(f"{name}={v} must be between 0 and 1")
    for name in ("min_detected", "min_agree"):
        v = getattr(p, name)
        if not 1 <= v <= p.n_frames:
            problems.append(f"{name}={v} must be between 1 and n_frames ({p.n_frames})")
    if p.second_obj_conf < p.det_conf:
        problems.append("second_obj_conf must be >= det_conf")
    if Verdict.REJECT not in p.class_map.values():
        problems.append("class_map must send at least one class (e.g. OTHER) to REJECT")
    for v, (lo, hi) in p.mass_ranges_g.items():
        if lo > hi:
            problems.append(f"mass range for {v.value} has min > max")
    if problems:
        raise ConfigError("invalid decision config:\n  - " + "\n  - ".join(problems))


def serial_commands(cfg: dict[str, Any]) -> dict[Verdict, str]:
    """Outcome -> serial command. Every outcome must have one."""
    raw = cfg["serial"]["commands"]
    cmds = {Verdict(str(k).upper()): str(v) for k, v in raw.items()}
    missing = [v.value for v in Verdict if v not in cmds]
    if missing:
        raise ConfigError(f"serial.commands is missing: {', '.join(missing)}")
    return cmds
