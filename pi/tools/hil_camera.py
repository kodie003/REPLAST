"""HIL test 1: does the C270 camera work from Python?

Opens the camera exactly like the main program, measures frames per second,
checks image size and brightness, and saves a few sample photos so you can
look at them (is the platform in view? in focus? well lit?).

    python3 tools/hil_camera.py
    python3 tools/hil_camera.py --show     # also show a live window (needs a screen)

No Arduino needed.
"""

import time
from datetime import datetime

from hil_common import RESULTS_DIR, HilRun, base_parser

from replast.camera import CameraError, camera_from_config, save_frames


def main() -> None:
    p = base_parser(__doc__)
    p.add_argument("--frames", type=int, default=60, help="frames to time (default 60)")
    p.add_argument("--show", action="store_true", help="show a live preview window; press q to close")
    args = p.parse_args()
    run = HilRun("hil_camera", args)
    cfg = run.cfg

    cam = camera_from_config(cfg)
    cam.flush_frames = 0
    cam.frame_interval_s = 0
    try:
        cam.open()
        run.check(f"camera {cfg['camera']['index']} opens", True)
    except CameraError as e:
        run.check(f"camera {cfg['camera']['index']} opens", False, str(e))
        print("  Check: ls /dev/video*   (the C270 is usually /dev/video0)")
        run.finish()

    try:
        t0 = time.monotonic()
        frames = cam.capture(args.frames)
        dt = time.monotonic() - t0
    except CameraError as e:
        run.check("capture frames", False, str(e))
        cam.close()
        run.finish()

    fps = len(frames) / dt
    run.record(check="fps", value=round(fps, 1))
    run.check("frame rate >= 10 fps", fps >= 10, f"{fps:.1f} fps")

    h, w = frames[0].shape[:2]
    want = (int(cfg["camera"]["height"]), int(cfg["camera"]["width"]))
    run.record(check="size", value=f"{w}x{h}")
    run.check("frame size matches config", (h, w) == want, f"{w}x{h}, config {want[1]}x{want[0]}")

    brightness = float(frames[-1].mean())
    run.record(check="brightness", value=round(brightness, 1))
    run.check("image not black or blown out", 20 <= brightness <= 235,
              f"mean brightness {brightness:.0f} (0 = black, 255 = white)")

    folder = RESULTS_DIR / f"hil_camera_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    saved = save_frames([frames[0], frames[len(frames) // 2], frames[-1]], folder, "sample")
    run.check("sample photos saved", len(saved) == 3, str(folder))
    print("  Open them and check: whole platform in view, sharp, evenly lit.")

    if args.show:
        import cv2

        print("Live preview - press q in the window to close.")
        while True:
            frame = cam.capture(1)[0]
            cv2.imshow("REPLAST camera", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        cv2.destroyAllWindows()

    cam.close()
    run.finish()


if __name__ == "__main__":
    main()
