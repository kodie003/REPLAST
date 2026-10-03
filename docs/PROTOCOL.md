# REPLAST serial protocol (Pi ⇄ Arduino)

## The idea in one paragraph

The Pi and the Arduino talk over the USB cable by sending short lines of
plain text, like text messages. The Pi is the boss: it decides **what** to do
(e.g. "sort this into paper"). The Arduino is the worker: it decides **how**
(which motor, how many steps) and reports back. Every order gets two replies:
**ACK** ("got it, starting") straight away, and **DONE** ("finished, platform is
back home") when the movement is over. If a reply doesn't arrive in time, the Pi
assumes something is wrong and stops sending movement orders.

## Link settings

| Setting | Value |
|---|---|
| Port on the Pi | `/dev/ttyACM0` (confirmed 3 Oct 2026) |
| Baud | 9600 |
| Format | ASCII text, one message per line, ends with `\n` (a `\r` before it is ignored) |
| Max line length | 32 characters |
| Case | Commands are UPPERCASE |

## Pi → Arduino (orders)

| Command | Meaning | Replies |
|---|---|---|
| `PING` | "Are you alive?" | `PONG` |
| `SORT_PET` | Tip into compartment 1 (home, no rotation) | `ACK:SORT_PET` … `DONE:SORT_PET` |
| `SORT_PAPER` | Rotate 90° counter-clockwise, tip, return home | `ACK:SORT_PAPER` … `DONE:SORT_PAPER` |
| `SORT_AL` | Rotate 180° counter-clockwise, tip, return home | `ACK:SORT_AL` … `DONE:SORT_AL` |
| `REJECT` | Rotate 90° clockwise, tip, return home | `ACK:REJECT` … `DONE:REJECT` |
| `RESET` | Level the servo, drive the stepper back to step 0, clear any error/stop | `ACK:RESET` … `DONE:RESET` |
| `STOP` | Software stop: halt motors now, refuse movement until `RESET` | `STOPPED` |

There is no physical E-stop and no load cell, so there is no `WEIGH` or `TARE`.
`STOP` is the software stand-in: the Pi sends it on shutdown, on Ctrl+C and
when something goes wrong mid-cycle.

## Arduino → Pi (reports)

| Message | When |
|---|---|
| `READY` | Once, after power-on/reset. Platform is at home, servo level. |
| `PONG` | Reply to `PING`. |
| `OBJECT` | The IR sensor has seen an item on the platform **steadily** (not a flicker). Sent once per item. |
| `CLEAR` | The platform is empty again. |
| `ACK:<cmd>` | Order received and accepted; movement starting. |
| `DONE:<cmd>` | Movement finished and platform back at home. |
| `ERR:<code>` | Something went wrong (see below). |
| `STOPPED` | Motors halted after `STOP`. |

Lines starting with `#` are debug comments; the Pi ignores them. Any other
unknown line is also ignored (and logged), never acted on.

## Error codes

| Code | Meaning | What the Pi does |
|---|---|---|
| `ERR:BUSY` | An order arrived while a move was running | Treats the cycle as failed; waits, then `RESET` |
| `ERR:UNKNOWN_CMD` | Misspelt/unknown order | Logs a software bug; no movement happened |
| `ERR:STOPPED` | Movement order arrived after `STOP` and before `RESET` | Sends `RESET` only when it's safe |
| `ERR:TIMEOUT` | A move took longer than the firmware's own limit | Stops the cycle, logs an error |

## A normal cycle, line by line

```
Arduino → Pi   OBJECT
                (Pi takes 5 photos, runs the model, decides PAPER)
Pi → Arduino   SORT_PAPER
Arduino → Pi   ACK:SORT_PAPER          (within 1 s)
                (rotate 90° CCW, tip right, level, rotate back home)
Arduino → Pi   DONE:SORT_PAPER         (within 15 s)
Arduino → Pi   CLEAR                   (item has fallen off)
```

## Timing rules on the Pi (all in `pi/config.yaml`)

| Setting | Default | Why |
|---|---|---|
| `ack_timeout_s` | 1.0 | ACK is sent before any movement, so it should be near-instant |
| `done_timeout_s` | 15.0 | Longest route (aluminium, 180° there and back + tip) takes roughly 5 s; 3× margin |
| `ping_retries` | 3 | One lost line shouldn't kill the link; three in a row means it's dead |
| `ready_timeout_s` | 4.0 | Opening the port may restart the Arduino; wait this long for `READY` |

If ACK or DONE is late, the Pi sends `STOP`, logs an error and does not send
another movement order until a `RESET` has completed.

## Finding and fixing the port on Ubuntu

```bash
ls /dev/ttyACM*            # expect: /dev/ttyACM0
groups                     # does the list include "dialout"?
sudo usermod -aG dialout $USER   # if not; then log out and back in (or reboot)
```

"Permission denied: '/dev/ttyACM0'" means the `dialout` step is needed.
Close the Arduino IDE's Serial Monitor before running Pi code — only one
program can hold the port at a time.
