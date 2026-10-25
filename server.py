#!/usr/bin/env python3
"""FTP drop box for the Z8's built-in Wi-Fi FTP upload.

The camera joins the home Wi-Fi and sends each shot here. Files land flat in
~/QDAM/Listings/Incoming (C:\\QDAM\\Listings\\Incoming on Windows). Every login
and upload is logged to events.jsonl; watcher.py and measure.py read it.

Login is made on first run, never in git: ~/.config/z8-wifi-drop/login.json,
or %APPDATA%\\z8-wifi-drop\\login.json on Windows.
"""
import json
import secrets
import string
import sys
import time
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

import common
import exif

# e=cwd l=list/size w=store a=append (resume) m=mkdir f=rename T=set mtime.
# No d: the camera never needs to delete what it sent.
PERMS = "elwamfT"


def load_login(path):
    if path.exists():
        return json.loads(path.read_text())
    # Short and lowercase: it is typed on the camera's keyboard. The firewall
    # only lets the home LAN reach the port.
    pw = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    login = {"user": "z8", "password": pw}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(login, indent=2) + "\n")
    path.chmod(0o600)
    return login


class DropHandler(FTPHandler):
    events = common.EVENTS

    def on_login(self, username):
        common.log_event(self.events, "login", ip=self.remote_ip, user=username)

    def ftp_SIZE(self, path):
        # pyftpdlib refuses SIZE in ASCII mode. A client checking how much of
        # a file arrived before it resumes may ask before TYPE I, so answer.
        kept, self._current_type = self._current_type, "i"
        try:
            return super().ftp_SIZE(path)
        finally:
            self._current_type = kept

    def ftp_STOR(self, file, mode="w"):
        # Photos only: never rewrite line endings, even if TYPE A was sent.
        self._current_type = "i"
        resumed = bool(self._restart_position) or "a" in mode
        ok = super().ftp_STOR(file, mode)
        if ok:
            self._stor = (file, time.time(), resumed)
            common.log_event(self.events, "start", path=str(file), file=Path(file).name,
                             resumed=resumed, ip=self.remote_ip)
        return ok

    def _finish(self, ev, file):
        _, start, resumed = getattr(self, "_stor", (file, time.time(), False))
        self.__dict__.pop("_stor", None)
        end = time.time()
        size = Path(file).stat().st_size if Path(file).exists() else 0
        secs = max(end - start, 1e-3)
        extra = {}
        if ev == "done" and Path(file).suffix.lower() in common.PHOTO_EXTS:
            if not exif.jpeg_complete(file):
                ev = "partial"  # the link closed early; see exif.jpeg_complete
        if ev == "done" and Path(file).suffix.lower() in common.PHOTO_EXTS:
            # Log the shutter time now: the watcher may move the file at once.
            shot = exif.shot_time(file)
            if shot:
                extra["shot"] = round(shot.timestamp(), 3)
        rec = common.log_event(
            self.events, ev, path=str(file), file=Path(file).name, bytes=size,
            start=round(start, 3), secs=round(secs, 3),
            mbps=round(size * 8 / 1e6 / secs, 1), resumed=resumed, ip=self.remote_ip,
            **extra,
        )
        print(f"{ev} {rec['file']} {size / 1e6:.1f} MB in {rec['secs']} s from {rec['ip']}", flush=True)

    def on_file_received(self, file):
        self._finish("done", file)

    def on_disconnect(self):
        # The link died before the data channel opened: no callback fires.
        if hasattr(self, "_stor"):
            self._finish("partial", self._stor[0])

    def on_incomplete_file_received(self, file):
        # Keep it: with Keep Connection on, the camera resends or resumes it.
        # The watcher skips files whose last event is not "done".
        self._finish("partial", file)


def build_server(incoming, events, login, host="0.0.0.0", port=common.PORT):
    incoming.mkdir(parents=True, exist_ok=True)
    auth = DummyAuthorizer()
    auth.add_user(login["user"], login["password"], str(incoming), perm=PERMS)
    handler = type("Handler", (DropHandler,), {
        "authorizer": auth,
        "passive_ports": common.PASSIVE_PORTS,
        "banner": "z8-wifi-drop ready",
        "events": events,
    })
    return FTPServer((host, port), handler)


def main():
    common.STATE.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        # pythonw.exe (the Windows startup task) has no console; keep a log file.
        sys.stdout = sys.stderr = open(common.STATE / "server.log", "a", buffering=1)
    login = load_login(common.LOGIN)
    server = build_server(common.INCOMING, common.EVENTS, login)
    print(f"listening on :{common.PORT}, saving to {common.INCOMING}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
