"""Tests for replast/config.py, including a regression check on the shipped config.yaml."""

import copy

import pytest

from replast.config import ConfigError, decision_params, load_config, serial_commands
from replast.decision import Verdict


@pytest.fixture
def cfg():
    return load_config()


def test_shipped_config_matches_project_doc(cfg):
    """If any of these change, it must be a deliberate, documented decision."""
    p = decision_params(cfg)
    assert p.n_frames == 5
    assert p.det_conf == 0.25
    assert p.min_detected == 4
    assert p.min_agree == 4
    assert p.mean_conf == 0.70
    assert p.second_obj_conf == 0.50
    assert p.mass_check_enabled is False  # no load cell fitted
    assert p.class_map == {
        "PLASTIC": Verdict.PET,
        "METAL": Verdict.ALUMINIUM,
        "PAPER": Verdict.PAPER,
        "OTHER": Verdict.REJECT,
    }


def test_shipped_serial_commands(cfg):
    assert serial_commands(cfg) == {
        Verdict.PET: "SORT_PET",
        Verdict.ALUMINIUM: "SORT_AL",
        Verdict.PAPER: "SORT_PAPER",
        Verdict.REJECT: "REJECT",
    }


def test_model_settings_match_known_good_camera_script(cfg):
    assert cfg["model"]["imgsz"] == 512
    assert (cfg["camera"]["index"], cfg["camera"]["width"], cfg["camera"]["height"]) == ("auto", 640, 480)
    assert cfg["serial"]["baud"] == 9600


@pytest.mark.parametrize(
    "key, value",
    [
        ("mean_conf", 1.5),
        ("det_conf", -0.1),
        ("min_detected", 6),
        ("min_agree", 0),
        ("second_obj_conf", 0.1),  # below det_conf
        ("n_frames", 0),
    ],
)
def test_bad_values_are_refused(cfg, key, value):
    bad = copy.deepcopy(cfg)
    bad["decision"][key] = value
    with pytest.raises(ConfigError):
        decision_params(bad)


def test_unknown_outcome_in_class_map_is_refused(cfg):
    bad = copy.deepcopy(cfg)
    bad["decision"]["class_map"]["PLASTIC"] = "GLASS_BIN"
    with pytest.raises(ConfigError):
        decision_params(bad)


def test_class_map_without_reject_is_refused(cfg):
    bad = copy.deepcopy(cfg)
    bad["decision"]["class_map"]["OTHER"] = "PAPER"
    with pytest.raises(ConfigError):
        decision_params(bad)


def test_inverted_mass_range_is_refused(cfg):
    bad = copy.deepcopy(cfg)
    bad["decision"]["mass_check"]["ranges_g"]["PET"] = [50, 10]
    with pytest.raises(ConfigError):
        decision_params(bad)


def test_missing_decision_section(cfg):
    with pytest.raises(ConfigError):
        decision_params({})


def test_missing_serial_command_is_refused(cfg):
    bad = copy.deepcopy(cfg)
    del bad["serial"]["commands"]["REJECT"]
    with pytest.raises(ConfigError):
        serial_commands(bad)


def test_empty_file_is_refused(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    with pytest.raises(ConfigError):
        load_config(p)
