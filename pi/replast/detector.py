"""Runs the YOLO11n NCNN model on camera frames, like pi/known_good/test_camera.py.

Returns plain Detection objects (class name + confidence) that decision.py
understands, so nothing else in the program depends on Ultralytics.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from replast.decision import Detection


class ModelError(RuntimeError):
    pass


def boxes_to_detections(names: Mapping[int, str], classes: Sequence[float], confs: Sequence[float]) -> list[Detection]:
    """Turn the model's class ids + confidences into Detection objects."""
    out = []
    for c, conf in zip(classes, confs):
        name = names.get(int(c), f"CLASS_{int(c)}")  # unknown id -> rejected later by decide()
        out.append(Detection(str(name).upper(), float(conf)))
    return out


class Detector:
    def __init__(self, model_path: str, imgsz: int = 512, det_conf: float = 0.25):
        if not Path(model_path).exists():
            raise ModelError(
                f"model folder not found: {model_path}\n"
                "  Set model.path in pi/config.yaml to the folder that contains model.ncnn.param"
            )
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ModelError("ultralytics is not installed: pip install -r requirements-vision.txt") from e
        try:
            self.model = YOLO(model_path, task="detect")  # point at the FOLDER, not a file in it
        except Exception as e:
            raise ModelError(f"could not load model from {model_path}: {e}") from e
        self.imgsz = imgsz
        self.det_conf = det_conf
        self.last_times_s: list[float] = []

    def detect(self, frame: Any) -> list[Detection]:
        try:
            r = self.model(frame, imgsz=self.imgsz, conf=self.det_conf, verbose=False)[0]
        except Exception as e:
            raise ModelError(f"inference failed: {e}") from e
        return boxes_to_detections(r.names, r.boxes.cls.tolist(), r.boxes.conf.tolist())

    def warm_up(self, width: int = 640, height: int = 480) -> float:
        """Run once on a blank image. The first inference is much slower (~1.5 s)
        than the rest (~0.1 s), so do it at start-up, not on the first item."""
        import numpy as np

        t0 = time.monotonic()
        self.detect(np.zeros((height, width, 3), dtype="uint8"))
        return time.monotonic() - t0

    def detect_many(self, frames: Sequence[Any]) -> list[list[Detection]]:
        """Detections for each frame; per-frame times are kept in last_times_s."""
        out, times = [], []
        for frame in frames:
            t0 = time.monotonic()
            out.append(self.detect(frame))
            times.append(time.monotonic() - t0)
        self.last_times_s = times
        return out


def detector_from_config(cfg: dict) -> Detector:
    return Detector(
        model_path=str(cfg["model"]["path"]),
        imgsz=int(cfg["model"]["imgsz"]),
        det_conf=float(cfg["decision"]["det_conf"]),
    )
