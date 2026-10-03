"""The hardware test scripts must keep working: run each one against the simulator."""

import subprocess
import sys
from pathlib import Path

import pytest

PI_DIR = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("script, extra", [("hil_serial.py", []), ("hil_mechanism.py", ["--repeats", "2"])])
def test_hil_script_passes_in_sim(script, extra, tmp_path, monkeypatch):
    r = subprocess.run(
        [sys.executable, str(PI_DIR / "tools" / script), "--sim", *extra],
        capture_output=True, text=True, timeout=60, cwd=tmp_path,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FAIL" not in r.stdout
    assert ": PASS" in r.stdout


@pytest.mark.parametrize("script", ["hil_camera.py", "hil_latency.py"])
def test_camera_scripts_start(script, tmp_path):
    """These need a real camera/model, so only check they load and show their help."""
    r = subprocess.run([sys.executable, str(PI_DIR / "tools" / script), "--help"],
                       capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout
