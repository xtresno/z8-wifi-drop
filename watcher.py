#!/usr/bin/env python3
"""Sort new photos from Incoming into one folder per item.

Type an item ID (for example LAMP-042) and press Enter. Each new JPEG in
Incoming is renamed LAMP-042_01.jpg, LAMP-042_02.jpg, ... and moved to
Listings/LAMP-042/. Type a new ID for the next item. Enter alone shows the
status. q quits.

A file is moved only when it is complete: the FTP server logged it "done", or
it is a whole JPEG (ends with the FF D9 marker) that has not changed for
SETTLE seconds. A cut-off upload never ends with FF D9, so it stays put.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

import common
import exif

SETTLE = 5.0
DUPES = "_duplicates"
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


def clean_id(text):
    """Upper-case item ID safe as a folder name on Linux and Windows, or None."""
    wid = text.strip().upper()
    if re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,39}", wid) and wid not in RESERVED:
        return wid
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class EventTail:
    """Reads events.jsonl as it grows and keeps the last state per file."""

    def __init__(self, path):
        self.path = path
        self.pos = 0
        self.states = {}

    def update(self):
        try:
            with open(self.path, "rb") as f:
                if os.fstat(f.fileno()).st_size < self.pos:
                    self.pos = 0  # log was cleared
                f.seek(self.pos)
                data = f.read()
        except FileNotFoundError:
            return self.states
        end = data.rfind(b"\n") + 1  # leave a half-written line for next time
        self.pos += end
        for line in data[:end].splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("ev") in ("start", "done", "partial"):
                self.states[os.path.realpath(rec["path"])] = rec
        return self.states


class Sorter:
    def __init__(self, incoming, listings, events):
        self.incoming = Path(incoming)
        self.listings = Path(listings)
        self.tail = EventTail(events)
        self.seen = {}  # path -> (size, mtime, first time seen with that size)
        self.hashes = {}  # target folder -> {sha256}

    def ready(self, now=None):
        """Complete JPEGs in Incoming, oldest shot first."""
        now = time.time() if now is None else now
        states = self.tail.update()
        out = []
        for f in self.incoming.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in common.PHOTO_EXTS:
                continue
            if DUPES in f.relative_to(self.incoming).parts:
                continue
            try:
                st = f.stat()
            except FileNotFoundError:
                continue
            rec = states.get(os.path.realpath(f))
            if rec and rec["ev"] == "done" and rec["bytes"] == st.st_size:
                out.append(f)  # the server saw it finish: no need to wait
                continue
            # Anything else (hand copies, uploads the server lost track of)
            # must hold still for SETTLE seconds and be a whole JPEG.
            key = (st.st_size, st.st_mtime)
            prev = self.seen.get(f)
            if prev is None or prev[:2] != key:
                self.seen[f] = (*key, now)
            elif now - prev[2] >= SETTLE and now - st.st_mtime >= SETTLE and exif.jpeg_complete(f):
                out.append(f)

        def order(f):
            shot = exif.shot_time(f)
            return (shot.timestamp() if shot else f.stat().st_mtime, f.name)

        return sorted(out, key=order)

    def _target_hashes(self, folder):
        if folder not in self.hashes:
            self.hashes[folder] = {sha256(p) for p in folder.glob("*.jpg")}
        return self.hashes[folder]

    def next_number(self, wid, folder):
        pat = re.compile(rf"{re.escape(wid)}_(\d+)\.jpg", re.IGNORECASE)
        nums = [int(m.group(1)) for p in folder.glob("*") if (m := pat.fullmatch(p.name))]
        return max(nums, default=0) + 1

    def sort_into(self, wid, now=None):
        """Move every ready photo into Listings/<wid>/. Returns (src, dest) pairs."""
        folder = self.listings / wid
        folder.mkdir(parents=True, exist_ok=True)
        moves = []
        for src in self.ready(now):
            digest = sha256(src)
            hashes = self._target_hashes(folder)
            if digest in hashes:
                dest = self._unique(self.incoming / DUPES / src.name)
            else:
                dest = folder / f"{wid}_{self.next_number(wid, folder):02d}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            if dest.parent == folder:
                hashes.add(digest)
            self.seen.pop(src, None)
            moves.append((src, dest))
        return moves

    @staticmethod
    def _unique(path):
        n = 1
        out = path
        while out.exists():
            out = path.with_name(f"{path.stem}-{n}{path.suffix}")
            n += 1
        return out


def main():
    sorter = Sorter(common.INCOMING, common.LISTINGS, common.EVENTS)
    common.INCOMING.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    state = {"wid": None, "count": 0, "stop": False}

    waiting = sum(1 for f in common.INCOMING.iterdir() if f.suffix.lower() in common.PHOTO_EXTS)
    print(f"Watching {common.INCOMING}")
    if waiting:
        print(f"{waiting} photo(s) already waiting. They go to the first item ID you type.")
    print("Type an item ID and press Enter. Enter alone = status. q = quit.")

    def loop():
        while not state["stop"]:
            with lock:
                wid = state["wid"]
                if wid:
                    try:
                        for src, dest in sorter.sort_into(wid):
                            if dest.parent.name == DUPES:
                                print(f"  same photo already in {wid}: {src.name} -> {dest}", flush=True)
                            else:
                                state["count"] += 1
                                print(f"  {src.name} -> {dest}", flush=True)
                    except OSError as err:
                        print(f"  error: {err}. Trying again.", flush=True)
            time.sleep(1.0)

    threading.Thread(target=loop, daemon=True).start()
    try:
        for line in sys.stdin:
            text = line.strip()
            if text.lower() in ("q", "quit", "exit"):
                break
            if not text:
                with lock:
                    print(f"Item: {state['wid'] or '(none yet)'}. Photos for this item: {state['count']}.")
                continue
            wid = clean_id(text)
            if not wid:
                print("Use letters, digits, - or _ (up to 40). Try again.")
                continue
            with lock:
                state["wid"], state["count"] = wid, 0
                n = sorter.next_number(wid, common.LISTINGS / wid)
            print(f"Now sorting into {common.LISTINGS / wid} (next: {wid}_{n:02d}.jpg)")
    except KeyboardInterrupt:
        pass
    state["stop"] = True
    print("Stopped.")


if __name__ == "__main__":
    main()
