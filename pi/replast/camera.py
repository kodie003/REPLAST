"""Logitech C270 capture with OpenCV (V4L2), based on pi/known_good/test_camera.py.

The camera stays open for the whole run (opening it takes ~1 s). Because
the webcam keeps a few old frames in its buffer, every capture first throws
away `flush_frames` frames, so the photos show the item that is on the
platform NOW, not the empty platform from a moment ago.
"""

from __future__ import annotations

import glob
import time
from pathlib import Path
from typing import Any, Callable, Optional, Union


class CameraError(RuntimeError):
    pass


def candidate_devices(
    by_id: Optional[list[str]] = None, video_nodes: Optional[list[str]] = None
) -> list[str]:
    """Where to look for the USB webcam, best guess first.

    On a Raspberry Pi 5 the first /dev/video* numbers belong to the Pi's own
    video chips (they are NOT cameras), and the C270 lands on a higher number
    that can change between boots. /dev/v4l/by-id/ gives USB cameras a
    stable name, so try those first (Logitech / C270 before anything else),
    then every /dev/video* node in order.
    """
    if by_id is None:
        by_id = sorted(glob.glob("/dev/v4l/by-id/*-video-index0"))
    if video_nodes is None:
        video_nodes = sorted(glob.glob("/dev/video*"), key=lambda p: int("0" + "".join(c for c in p if c.isdigit())))
    logitech = [p for p in by_id if "046d" in p.lower() or "c270" in p.lower() or "logitech" in p.lower()]
    others = [p for p in by_id if p not in logitech]
    out: list[str] = []
    for p in logitech + others + video_nodes:
        if p not in out:
            out.append(p)
    return out


class Camera:
    def __init__(
        self,
        index: Union[int, str] = "auto",   # "auto", a number, or a path like /dev/video8
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
        self.device: Optional[str] = None   # which device actually opened

    def _try_open(self, cv2: Any, dev: Union[int, str]) -> Any:
        """Open dev and read one frame; None if it is not a working camera."""
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            return None
        return cap

    def open(self) -> None:
        import cv2  # imported here so unit tests run without OpenCV

        try:
            cv2.setLogLevel(0)  # hide OpenCV's warnings while probing non-camera devices
        except AttributeError:
            pass
        if str(self.index).lower() == "auto":
            tried = candidate_devices()
        else:
            tried = [int(self.index) if str(self.index).isdigit() else str(self.index)]
        cap = None
        for dev in tried:
            cap = self._try_open(cv2, dev)
            if cap is not None:
                self.device = str(dev)
                break
        if cap is None:
            raise CameraError(
                f"no working camera found (tried: {', '.join(map(str, tried)) or 'nothing - no /dev/video* at all'}). "
                "Is the C270 plugged in? List cameras with: v4l2-ctl --list-devices"
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
        index=c.get("index", "auto"),
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
