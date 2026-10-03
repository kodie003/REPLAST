"""HIL test 2: is the camera + model fast enough on the Pi?

For each of N items: take the 5 photos, run the model on each, apply the
reject rule, and time every part. Put a real item under the camera (you can
swap items between rounds) - the script also prints what the model saw.

    python3 tools/hil_latency.py               # 10 items
    python3 tools/hil_latency.py --items 20

PASS if the average time from "start taking photos" to "decision made" is
within the budget (default 4.0 s), which leaves room inside the 5 s target
(item detected -> sorting action) for the IR settle time and the ACK.
No Arduino needed.
"""

import time
from pathlib import Path

from hil_common import HilRun, base_parser

from replast.camera import CameraError, camera_from_config
from replast.config import decision_params
from replast.decision import decide
from replast.detector import ModelError, detector_from_config


def cpu_temp_c():
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def main() -> None:
    p = base_parser(__doc__)
    p.add_argument("--items", type=int, default=10)
    p.add_argument("--budget", type=float, default=4.0, help="max average seconds per item (default 4.0)")
    p.add_argument("--pause", type=float, default=1.0, help="seconds between items (to swap the item)")
    args = p.parse_args()
    run = HilRun("hil_latency", args)
    cfg = run.cfg
    params = decision_params(cfg)

    t0 = time.monotonic()
    try:
        det = detector_from_config(cfg)
        det.warm_up(int(cfg["camera"]["width"]), int(cfg["camera"]["height"]))
    except ModelError as e:
        run.check("model loads", False, str(e))
        run.finish()
    run.check("model loads", True, f"{time.monotonic() - t0:.1f} s including first (warm-up) inference")

    cam = camera_from_config(cfg)
    try:
        cam.open()
    except CameraError as e:
        run.check("camera opens", False, str(e))
        run.finish()

    item_times, frame_times = [], []
    for i in range(1, args.items + 1):
        try:
            t0 = time.monotonic()
            frames = cam.capture(params.n_frames)
            t_cap = time.monotonic() - t0
            detections = det.detect_many(frames)
            d = decide(detections, params)
            t_item = time.monotonic() - t0
        except (CameraError, ModelError) as e:
            run.check(f"item {i}", False, str(e))
            continue
        item_times.append(t_item)
        frame_times += det.last_times_s
        mean_inf = 1000 * sum(det.last_times_s) / len(det.last_times_s)
        print(f"  item {i}: capture {t_cap:.2f} s, inference {mean_inf:.0f} ms/frame, "
              f"total {t_item:.2f} s -> {d.verdict.value} ({d.reason.value}, "
              f"{d.winning_class or 'nothing'} {d.mean_conf:.2f})")
        run.record(item=i, capture_s=round(t_cap, 3), inference_ms_per_frame=round(mean_inf, 1),
                   item_s=round(t_item, 3), verdict=d.verdict.value, reason=d.reason.value,
                   winning_class=d.winning_class, mean_conf=round(d.mean_conf, 3), cpu_temp_c=cpu_temp_c())
        time.sleep(args.pause)
    cam.close()

    if not item_times:
        run.check("timed at least one item", False)
        run.finish()
    avg = sum(item_times) / len(item_times)
    worst = max(item_times)
    avg_frame = 1000 * sum(frame_times) / len(frame_times)
    temp = cpu_temp_c()
    print(f"\n  Inference per frame: {avg_frame:.0f} ms average, {1000 * max(frame_times):.0f} ms worst")
    print(f"  Whole item (5 photos + 5 inferences + decision): {avg:.2f} s average, {worst:.2f} s worst")
    if temp is not None:
        print(f"  CPU temperature now: {temp:.0f} C (above ~80 C the Pi slows down)")
    run.check(f"average item time <= {args.budget:.1f} s", avg <= args.budget, f"{avg:.2f} s")
    run.finish()


if __name__ == "__main__":
    main()
