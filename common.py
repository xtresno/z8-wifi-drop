"""Paths and the shared event log for the wifi-drop tools."""
import json
import os
import time
from pathlib import Path

PORT = 2121  # above 1024, so no root needed on Linux
PASSIVE_PORTS = range(60000, 60101)  # must match the firewall rule
PHOTO_EXTS = {".jpg", ".jpeg"}

if os.name == "nt":
    LISTINGS = Path(r"C:\QDAM\Listings")
    # Not %APPDATA%: the startup task runs before login, where it may be unset.
    STATE = Path.home() / "AppData/Roaming/z8-wifi-drop"
    LOGIN = STATE / "login.json"
else:
    LISTINGS = Path.home() / "QDAM/Listings"
    STATE = Path.home() / ".local/state/z8-wifi-drop"
    LOGIN = Path.home() / ".config/z8-wifi-drop/login.json"

# Z8_DROP_LISTINGS moves the photo folders (set it before the server starts).
if os.environ.get("Z8_DROP_LISTINGS"):
    LISTINGS = Path(os.environ["Z8_DROP_LISTINGS"])

INCOMING = LISTINGS / "Incoming"
EVENTS = STATE / "events.jsonl"


def log_event(log, ev, **fields):
    """Append one event to `log`. Events: login, start, done, partial."""
    rec = {"ev": ev, "t": round(time.time(), 3), **fields}
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def read_events(path, since=0.0):
    """All events at or after `since`. Skips a torn last line."""
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("t", 0) >= since:
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


def file_states(events):
    """Latest upload event per file path: 'start', 'done' or 'partial'."""
    states = {}
    for rec in events:
        if rec["ev"] in ("start", "done", "partial"):
            states[rec["path"]] = rec
    return states
