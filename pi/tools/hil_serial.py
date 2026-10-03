"""HIL test 3: does the Pi <-> Arduino link work, and does every command behave?

Run on the Pi with replast_controller.ino flashed and the Serial Monitor closed:
    python3 tools/hil_serial.py
Try it without hardware first:
    python3 tools/hil_serial.py --sim

THE PLATFORM WILL MOVE in part 4. Keep hands clear.
"""

from hil_common import HilRun, base_parser

from replast.serial_link import SerialLinkError


def main() -> None:
    args = base_parser(__doc__).parse_args()
    run = HilRun("hil_serial", args)
    link = run.connect()

    print("\n--- 1. PING round trips (x10) ---")
    times = []
    for i in range(10):
        t0 = run.now()
        link.send_line("PING")
        reply = link.read_reply(1.0)
        dt = run.now() - t0
        ok = reply == "PONG"
        times.append(dt)
        run.record(step="ping", n=i + 1, reply=reply, seconds=round(dt, 4), ok=ok)
    good = sum(1 for r in run.rows if r["step"] == "ping" and r["ok"])
    run.check("10/10 PINGs answered", good == 10, f"{good}/10, slowest {max(times) * 1000:.0f} ms")

    print("\n--- 2. STATUS ---")
    link.send_line("STATUS")
    reply = link.read_reply(1.0) or ""
    run.record(step="status", reply=reply)
    run.check("STATUS answered", reply.startswith("STATUS:"), reply)
    if "IR=" in reply:
        print("  (IR= is the live sensor reading; ITEM=1 means it thinks an item is on the platform)")

    print("\n--- 3. Unknown command is refused ---")
    link.send_line("HELLO")
    reply = link.read_reply(1.0)
    run.record(step="unknown", reply=reply)
    run.check("HELLO -> ERR:UNKNOWN_CMD", reply == "ERR:UNKNOWN_CMD", str(reply))

    print("\n--- 4. Every movement command (platform WILL move) ---")
    if not run.ask("Platform is lined up over PET (compartment 1) and hands are clear. Start?"):
        run.check("movement tests", False, "skipped by user")
        run.finish()
    for cmd in ["SORT_PET", "SORT_PAPER", "SORT_AL", "REJECT", "RESET"]:
        try:
            r = link.run_command(cmd)
            run.record(step="command", command=cmd, ack_s=round(r.ack_s, 3), done_s=round(r.done_s, 3), ok=True)
            run.check(f"{cmd}: ACK + DONE", True, f"ACK {r.ack_s * 1000:.0f} ms, DONE {r.done_s:.2f} s")
        except SerialLinkError as e:
            run.record(step="command", command=cmd, ok=False, error=str(e))
            run.check(f"{cmd}: ACK + DONE", False, str(e))
            run.recover()

    print("\n--- 5. STOP blocks movement until RESET ---")
    run.check("STOP -> STOPPED", link.stop())
    link.send_line("SORT_PET")
    reply = link.read_reply(1.0)
    run.record(step="after_stop", reply=reply)
    run.check("SORT_PET after STOP is refused", reply == "ERR:STOPPED", str(reply))
    try:
        link.run_command("RESET")
        run.check("RESET clears the stop", True)
    except SerialLinkError as e:
        run.check("RESET clears the stop", False, str(e))

    run.finish()


if __name__ == "__main__":
    main()
