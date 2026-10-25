#!/usr/bin/env python3
"""Measure the camera's Wi-Fi link and upload speed.

    python measure.py ping            # 60 s ping to the camera
    python measure.py timing          # wait for the next 5 JPEGs, time each
    python measure.py timing --clock-offset 2.5

The camera's IP comes from its last FTP login (use --ip to set it). Timing
uses the shutter time in EXIF, so the camera clock must match this PC's.
--clock-offset is camera minus PC, in seconds.
"""
import argparse
import os
import re
import statistics
import subprocess
import sys
import time

import common

MAX_LOSS = 2.0  # percent
MAX_SECS = 5.0  # per JPEG, shutter to file in Incoming
CLOCK_SUSPECT = 120.0


def camera_ip(events):
    for rec in reversed(common.read_events(events)):
        if rec["ev"] in ("login", "done"):
            return rec["ip"]
    return None


def parse_ping(out):
    """Loss percent and RTT ms (min, avg, max) from Linux or Windows ping."""
    loss = re.search(r"([\d.]+)% (?:packet )?loss", out)
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/", out)  # Linux
    if not rtt:
        win = re.search(r"Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms", out)
        rtt = win and (win.group(1), win.group(3), win.group(2))
    else:
        rtt = rtt.groups()
    return {
        "loss": float(loss.group(1)) if loss else 100.0,
        "rtt": tuple(float(x) for x in rtt) if rtt else None,
    }


def ping(ip, secs):
    if os.name == "nt":
        cmd = ["ping", "-n", str(secs), "-w", "2000", ip]
    else:
        cmd = ["ping", "-c", str(secs), "-i", "1", "-W", "2", ip]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return parse_ping(out)


def wait_for_jpegs(events, n, since, timeout):
    got = []
    deadline = time.time() + timeout
    while len(got) < n and time.time() < deadline:
        got = [r for r in common.read_events(events, since)
               if r["ev"] == "done" and r["file"].lower().endswith((".jpg", ".jpeg"))]
        time.sleep(0.5)
    return got[:n]


def hour_offset(recs):
    """Whole hours the camera clock is off (a time zone or DST slip), or 0.

    Real delays are a few seconds, so raw delays that all sit near the same
    multiple of 3600 s give the offset away.
    """
    raw = [r["t"] - r["shot"] for r in recs if r.get("shot")]
    if not raw:
        return 0
    hours = round(statistics.median(raw) / 3600)
    # -60: the clock can also be some seconds off on top of the hours.
    if hours and all(-60 <= d - hours * 3600 <= CLOCK_SUSPECT for d in raw):
        return -hours * 3600  # camera minus PC
    return 0


def timing_report(recs, clock_offset=0.0):
    """Rows per photo plus averages. delay = shutter to fully saved here."""
    rows = []
    for r in recs:
        shot = r.get("shot")
        delay = r["t"] - (shot - clock_offset) if shot else None
        rows.append({"file": r["file"], "mb": r["bytes"] / 1e6, "transfer": r["secs"],
                     "mbps": r["mbps"], "delay": delay})
    delays = [x["delay"] for x in rows if x["delay"] is not None]
    clock_ok = bool(delays) and all(0 <= d <= CLOCK_SUSPECT for d in delays)
    # Backlog needs no clock: how much later, relative to its own shutter,
    # the last photo arrived than the first. It grows when shots come faster
    # than the link can send them (for example RAW + JPEG uploads).
    shots = [r for r in recs if r.get("shot")]
    backlog = ((shots[-1]["t"] - shots[-1]["shot"]) - (shots[0]["t"] - shots[0]["shot"])
               if len(shots) > 1 else None)
    return {
        "backlog": backlog,
        "rows": rows,
        "avg_transfer": statistics.mean(x["transfer"] for x in rows) if rows else None,
        "avg_delay": statistics.mean(delays) if clock_ok else None,
        "clock_ok": clock_ok,
    }


def verdict(loss=None, secs=None):
    """Plain-language result. None when the link is fine."""
    bad = []
    if loss is not None and loss > MAX_LOSS:
        bad.append(f"packet loss is {loss:.1f}% (limit {MAX_LOSS}%)")
    if secs is not None and secs > MAX_SECS:
        bad.append(f"each JPEG takes {secs:.1f} s (limit {MAX_SECS:.0f} s)")
    if not bad:
        return None
    return ("The garage Wi-Fi is the bottleneck: " + " and ".join(bad) + ".\n"
            "Fix, best first:\n"
            "  1. Put a mesh node or access point in the garage (5 GHz).\n"
            "  2. Or wire the Z8: USB-C to Ethernet adapter, same FTP profile type 'Ethernet'.\n"
            "  3. Move the router or camera so no metal door or wall is between them.\n"
            "  4. Last resort: smaller JPEGs (Image size M, or JPEG Normal).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["ping", "timing"])
    ap.add_argument("--ip")
    ap.add_argument("--secs", type=int, default=60, help="ping length")
    ap.add_argument("-n", type=int, default=5, help="JPEGs to time")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--clock-offset", type=float, default=0.0)
    args = ap.parse_args()

    if args.what == "ping":
        ip = args.ip or camera_ip(common.EVENTS)
        if not ip:
            sys.exit("No camera IP yet. Connect the camera to the FTP server once, or pass --ip.")
        print(f"Pinging {ip} for {args.secs} s ...", flush=True)
        r = ping(ip, args.secs)
        if r["rtt"]:
            lo, avg, hi = r["rtt"]
            print(f"Loss {r['loss']:.1f}%   latency min {lo:.0f} / avg {avg:.0f} / max {hi:.0f} ms")
        else:
            print("No replies at all. The camera is off, asleep, or not on the Wi-Fi.")
            print("Check: 'FTP' shows in green on the camera screen, then run this again.")
            sys.exit(1)
        print(verdict(loss=r["loss"]) or "Link OK.")
        return

    since = time.time()
    print(f"Waiting for {args.n} JPEGs. Take the shots now.", flush=True)
    recs = wait_for_jpegs(common.EVENTS, args.n, since, args.timeout)
    if not recs:
        sys.exit("No JPEGs arrived.")
    offset = args.clock_offset
    if not offset and (offset := hour_offset(recs)):
        print(f"Camera clock is {offset / 3600:+.0f} h off this PC (time zone or daylight saving).")
        print("Corrected for it here. Fix it on the camera: Setup menu > Time zone and date.")
    rep = timing_report(recs, offset)
    print(f"{'file':<16}{'MB':>6}{'transfer s':>12}{'Mbit/s':>8}{'shutter->here s':>17}")
    for x in rep["rows"]:
        d = f"{x['delay']:.1f}" if x["delay"] is not None and rep["clock_ok"] else "?"
        print(f"{x['file']:<16}{x['mb']:>6.1f}{x['transfer']:>12.2f}{x['mbps']:>8.1f}{d:>17}")
    if rep["clock_ok"]:
        print(f"Average: {rep['avg_delay']:.1f} s per photo, shutter to folder.")
        secs = rep["avg_delay"]
    else:
        print("Camera clock does not match this PC, so shutter->here is unknown.")
        print(f"Average transfer alone: {rep['avg_transfer']:.1f} s per photo.")
        secs = rep["avg_transfer"]
    if rep["backlog"] is not None and rep["backlog"] > 2:
        print(f"Photos queued up: the last one waited {rep['backlog']:.0f} s longer than the first.")
        print("The camera may be sending RAW files too. Options > Upload RAW + JPEG as > JPEG only.")
    if len(recs) < args.n:
        print(f"Only {len(recs)} of {args.n} arrived before the timeout.")
    print(verdict(secs=secs) or "Speed OK.")


if __name__ == "__main__":
    main()
