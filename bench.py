#!/usr/bin/env python3
"""Benchmark the drop box on this PC (no camera needed).

    .venv/bin/python bench.py

1. Raw server speed: is this PC ever the bottleneck?
2. Timing check: does the logged transfer time match a known link speed?
3. Burst: 5 photos at once.
4. Watcher: extra delay from "upload done" to "file in the watch folder".
5. The Wi-Fi speed each JPEG size needs to meet the 5 s target.
"""
import io
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))

from helpers import LiveServer, fake_jpeg  # noqa: E402

import common  # noqa: E402
import watcher  # noqa: E402

JPEG_MB = 20  # Z8 JPEG Fine, Large: roughly 15-25 MB depending on the scene


def photo(mb, seed):
    with tempfile.TemporaryDirectory() as d:
        return fake_jpeg(Path(d) / "x.jpg", body=int(mb * 1e6), seed=seed).read_bytes()


def send(srv, name, blob, mbps=None):
    f = srv.client()
    f.voidcmd("TYPE I")
    conn = f.transfercmd(f"STOR {name}")
    if mbps is None:
        conn.sendall(blob)
    else:
        chunk = 64 * 1024
        t0 = time.perf_counter()
        for i in range(0, len(blob), chunk):
            conn.sendall(blob[i:i + chunk])
            ahead = (i + chunk) * 8 / (mbps * 1e6) - (time.perf_counter() - t0)
            if ahead > 0:
                time.sleep(ahead)
    conn.close()
    f.voidresp()
    f.quit()


def done(srv, n):
    end = time.time() + 120
    while time.time() < end:
        recs = [r for r in common.read_events(srv.events) if r["ev"] == "done"]
        if len(recs) >= n:
            return recs
        time.sleep(0.02)
    raise SystemExit("timed out waiting for uploads")


def main():
    blob = photo(JPEG_MB, 1)
    print(f"Test photo: {len(blob) / 1e6:.1f} MB\n")

    with LiveServer() as srv:
        cpu0 = time.process_time()
        for i in range(10):
            send(srv, f"RAW_{i}.JPG", blob)
        recs = done(srv, 10)
        cpu = time.process_time() - cpu0
        rate = statistics.median(r["mbps"] for r in recs)
        print("1. Raw server speed, 10 x 20 MB, sequential, this PC only")
        print(f"   median {rate:,.0f} Mbit/s, {statistics.median(r['secs'] for r in recs) * 1000:.0f} ms per photo, "
              f"CPU {cpu:.2f} s total (client + server)\n")

    print("2. Timing check, link held to a fixed speed (Wi-Fi stand-in)")
    print(f"   {'link Mbit/s':>12}{'expected s':>12}{'logged s':>10}{'error':>8}")
    for mbps in (20, 50, 100):
        with LiveServer() as srv:
            send(srv, "T.JPG", blob, mbps=mbps)
            rec = done(srv, 1)[0]
            want = len(blob) * 8 / (mbps * 1e6)
            print(f"   {mbps:>12}{want:>12.2f}{rec['secs']:>10.2f}{(rec['secs'] - want) / want:>8.1%}")
    print()

    with LiveServer() as srv:
        blobs = [photo(JPEG_MB, i + 2) for i in range(5)]
        ts = [threading.Thread(target=send, args=(srv, f"B_{i}.JPG", b, 40)) for i, b in enumerate(blobs)]
        t0 = time.time()
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        recs = done(srv, 5)
        wall = time.time() - t0
        ok = all((srv.incoming / f"B_{i}.JPG").stat().st_size == len(blob) for i in range(5))
        print("3. Burst: 5 photos at once, each held to 40 Mbit/s")
        print(f"   all 5 complete: {ok}, wall time {wall:.1f} s (expected {len(blob) * 8 / 40e6:.1f} s)\n")

    with LiveServer() as srv:
        s = watcher.Sorter(srv.incoming, srv.listings, srv.events)
        lags, stop = [], threading.Event()

        def loop():  # same 1 s poll as watcher.main
            while not stop.is_set():
                for src, dest in s.sort_into("BENCH"):
                    lags.append(time.time())
                stop.wait(1.0)

        threading.Thread(target=loop, daemon=True).start()
        small = [photo(5, 10 + i) for i in range(5)]
        for i, b in enumerate(small):
            send(srv, f"W_{i}.JPG", b)
            time.sleep(0.37)  # not in step with the poll
        end = time.time() + 10
        while len(lags) < 5 and time.time() < end:
            time.sleep(0.05)
        stop.set()
        recs = done(srv, 5)
        extra = [m - r["t"] for m, r in zip(sorted(lags), sorted(recs, key=lambda r: r["t"]))]
        moved = sorted(p.name for p in (srv.listings / "BENCH").iterdir())
        print("4. Watcher: upload done -> renamed in the watch folder")
        print(f"   {len(moved)} moved ({moved[0]} .. {moved[-1]}), extra delay avg {statistics.mean(extra):.2f} s, "
              f"max {max(extra):.2f} s\n")

    print("5. Wi-Fi speed needed for 5 s per JPEG (upload only; the camera also needs ~0.5-1 s to save)")
    for mb in (10, 15, 20, 25):
        print(f"   {mb:>3} MB JPEG -> at least {mb * 8 / 5:.0f} Mbit/s")

    try:
        import resource  # not on Windows
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"\nPeak memory of this run (server + test client): {rss:.0f} MB")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
