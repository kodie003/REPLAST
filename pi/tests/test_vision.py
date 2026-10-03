"""camera.py and detector.py without a real webcam or model."""

import pytest

from replast.camera import Camera, CameraError
from replast.detector import Detector, ModelError, boxes_to_detections
from replast.decision import Detection


def test_boxes_to_detections_uses_model_names():
    names = {0: "METAL", 1: "OTHER", 2: "PAPER", 3: "PLASTIC"}
    out = boxes_to_detections(names, [3.0, 0.0, 7.0], [0.91, 0.4, 0.5])
    assert out == [Detection("PLASTIC", 0.91), Detection("METAL", 0.4), Detection("CLASS_7", 0.5)]


def test_missing_model_folder_gives_clear_error(tmp_path):
    with pytest.raises(ModelError, match="model folder not found"):
        Detector(str(tmp_path / "nope"))


class FakeCap:
    def __init__(self, good=100):
        self.good = good
        self.grabs = 0
        self.reads = 0

    def grab(self):
        self.grabs += 1

    def read(self):
        self.reads += 1
        return (self.reads <= self.good, "img" if self.reads <= self.good else None)

    def release(self):
        pass


def test_capture_flushes_stale_frames_then_reads_n():
    cam = Camera(flush_frames=4, frame_interval_s=0.1, sleep=lambda s: None)
    cam.cap = FakeCap()
    assert cam.capture(5) == ["img"] * 5
    assert cam.cap.grabs == 4


def test_capture_failure_raises_camera_error():
    cam = Camera(sleep=lambda s: None)
    cam.cap = FakeCap(good=2)
    with pytest.raises(CameraError):
        cam.capture(5)


def test_capture_before_open_raises():
    with pytest.raises(CameraError):
        Camera().capture(5)


def test_real_model_if_available():
    """Runs the real NCNN model when it and ultralytics are installed (on the Pi); skipped otherwise."""
    pytest.importorskip("ultralytics")
    import os
    from replast.config import load_config
    path = os.environ.get("REPLAST_MODEL", load_config()["model"]["path"])
    if not os.path.isdir(path):
        pytest.skip(f"model not found at {path}")
    import numpy as np
    d = Detector(path, imgsz=512)
    assert d.warm_up() > 0
    out = d.detect_many([np.zeros((480, 640, 3), "uint8")] * 2)
    assert len(out) == 2 and all(isinstance(x, list) for x in out)
    assert len(d.last_times_s) == 2
