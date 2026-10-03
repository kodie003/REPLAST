"""HIL test 5: does every sort route move reliably and come back home?

No camera is used. Each route is run N times; after each route you confirm by
eye that the platform is back over PET (compartment 1) and level - this
catches lost steps, which the Arduino cannot detect on its own (no limit switch).

    python3 tools/hil_mechanism.py              # 5 runs per route
    python3 tools/hil_mechanism.py --repeats 20
    python3 tools/hil_mechanism.py --sim        # try without hardware

THE PLATFORM WILL MOVE. Keep hands clear.
"""

from hil_common import HilRun, base_parser

from replast.serial_link import SerialLinkError

ROUTES = ["SORT_PET", "SORT_PAPER", "SORT_AL", "REJECT"]


def main() -> None:
    p = base_parser(__doc__)
    p.add_argument("--repeats", type=int, default=5, help="runs per route (default 5)")
    p.add_argument("--skip-stop-test", action="store_true", help="skip the STOP-during-move test")
    args = p.parse_args()
    run = HilRun("hil_mechanism", args)
    link = run.connect()

    if not run.ask("Platform is lined up over PET (compartment 1) and hands are clear. Start?"):
        run.finish()

    for cmd in ROUTES:
        print(f"\n--- {cmd} x{args.repeats} ---")
        ok_count = 0
        for i in range(1, args.repeats + 1):
            try:
                r = link.run_command(cmd)
                ok_count += 1
                run.record(route=cmd, run=i, ok=True, done_s=round(r.done_s, 3))
                print(f"  run {i}: DONE in {r.done_s:.2f} s")
            except SerialLinkError as e:
                run.record(route=cmd, run=i, ok=False, error=str(e))
                print(f"  run {i}: FAILED - {e}")
                run.recover()
        run.check(f"{cmd}: {ok_count}/{args.repeats} moves completed", ok_count == args.repeats)
        home = run.ask(f"After {cmd}: is the platform exactly over PET and level?")
        run.record(route=cmd, run="visual_home_check", ok=home)
        run.check(f"{cmd}: returned home (visual check)", home)

    if not args.skip_stop_test:
        print("\n--- STOP during a move ---")
        link.send_line("SORT_AL")
        run.wait(0.5)               # let it start turning
        stopped = link.stop()
        run.check("STOP mid-move -> STOPPED", stopped)
        froze = run.ask("Did the platform stop turning immediately?")
        run.check("platform froze on STOP (visual)", froze)
        try:
            link.run_command("RESET")
            home = run.ask("After RESET: is the platform back over PET and level?")
            run.check("RESET brings it home after a STOP (visual)", home)
        except SerialLinkError as e:
            run.check("RESET after STOP", False, str(e))
        run.record(route="STOP_TEST", run=1, ok=stopped and froze)

    run.finish()


if __name__ == "__main__":
    main()
