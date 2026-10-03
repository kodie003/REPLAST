"""Logitech C270 capture with OpenCV (V4L2), based on pi/known_good/test_camera.py.

The camera stays open for the whole run (opening it takes ~1 s). Because
the webcam keeps a few old frames in its buffer, every capture first throws
away `flush_frames` frames, so the photos show the item that is on the
platform NOW, not the empty platform from a moment ago.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Optional


class CameraError(RuntimeError):
    pass


class Camera:
    def __init__(
        self,
        index: int = 0,
        width: int = 640,
        height: int = 480,
        flush_frames: int = 4,
        frame_interval_s: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.index = index
        self.width = width
        self.height = height
        self.flush_frames = flush_frames
        self.frame_interval_s = frame_interval_s
        self.sleep = sleep
        self.cap: Any = None

    def open(self) -> None:
        import cv2  # imported here so unit tests run without OpenCV

        cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.index)  # fall back to OpenCV's default backend
        if not cap.isOpened():
            raise CameraError(
                f"cannot open camera {self.index}. Is the C270 plugged in? Check: ls /dev/video*"
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # ask for a short buffer (not every driver obeys)
        self.cap = cap
        # Warm-up: the first frames after opening are often dark while auto-exposure settles.
        for _ in range(10):
            cap.read()

    def capture(self, n: int) -> list[Any]:
        """n fresh frames, frame_interval_s apart. Raises CameraError on failure."""
        if self.cap is None:
            raise CameraError("camera is not open")
        for _ in range(self.flush_frames):
            self.cap.grab()  # discard stale buffered frames
        frames = []
        for i in range(n):
            ok, frame = self.cap.read()
            if not ok or frame is None:
                raise CameraError(f"camera returned no image (frame {i + 1} of {n})")
            frames.append(frame)
            if i < n - 1 and self.frame_interval_s > 0:
                self.sleep(self.frame_interval_s)
        return frames

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


def camera_from_config(cfg: dict) -> Camera:
    c = cfg["camera"]
    return Camera(
        index=int(c["index"]),
        width=int(c["width"]),
        height=int(c["height"]),
        flush_frames=int(c.get("flush_frames", 4)),
        frame_interval_s=float(c.get("frame_interval_s", 0.1)),
    )


def save_frames(frames: list[Any], folder: Path, stem: str) -> list[Path]:
    """Save JPEGs (e.g. data/captures/20261003_101500_PAPER_0.jpg). Never raises."""
    paths = []
    try:
        import cv2

        folder.mkdir(parents=True, exist_ok=True)
        for i, frame in enumerate(frames):
            p = folder / f"{stem}_{i}.jpg"
            cv2.imwrite(str(p), frame)
            paths.append(p)
    except Exception:  # saving pictures must never stop the machine
        pass
    return paths
