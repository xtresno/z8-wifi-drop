"""Test helpers: fake Z8 JPEGs and a throwaway FTP server."""
import ftplib
import os
import random
import socket
import struct
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402

LOGIN = {"user": "z8", "password": "testpass"}


def fake_jpeg(path, when="2026:10:24 21:30:05", subsec="25", offset="-04:00", body=200_000, seed=0):
    """A JPEG-shaped file with a real EXIF block (big-endian, like Nikon)."""
    entries = [(0x9003, when.encode() + b"\0"), (0x9291, subsec.encode() + b"\0")]
    if offset:
        entries.append((0x9011, offset.encode() + b"\0"))
    # TIFF header, IFD0 at 8 with one entry: ExifIFD pointer.
    exif_ifd_at = 8 + 2 + 12 + 4
    data_at = exif_ifd_at + 2 + 12 * len(entries) + 4
    ifd0 = struct.pack(">H", 1) + struct.pack(">HHII", 0x8769, 4, 1, exif_ifd_at) + b"\0\0\0\0"
    ifd, blob = struct.pack(">H", len(entries)), b""
    for tag, val in entries:
        if len(val) <= 4:
            ifd += struct.pack(">HHI", tag, 2, len(val)) + val.ljust(4, b"\0")
        else:
            ifd += struct.pack(">HHII", tag, 2, len(val), data_at + len(blob))
            blob += val
    ifd += b"\0\0\0\0"
    tiff = b"MM\0*" + struct.pack(">I", 8) + ifd0 + ifd + blob
    app1 = b"Exif\0\0" + tiff
    rnd = os.urandom(body) if seed == 0 else random.Random(seed).randbytes(body)
    Path(path).write_bytes(b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
                           + b"\xff\xda" + rnd + b"\xff\xd9")
    return Path(path)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    """Real pyftpdlib server on a free port, in temp folders."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.incoming = root / "Listings/Incoming"
        self.listings = root / "Listings"
        self.events = root / "state/events.jsonl"
        self.port = free_port()
        self.srv = server.build_server(self.incoming, self.events, LOGIN, "127.0.0.1", self.port)
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"timeout": 0.05}, daemon=True)
        self.thread.start()
        return self

    def client(self, user=LOGIN["user"], password=LOGIN["password"]):
        f = ftplib.FTP()
        f.connect("127.0.0.1", self.port, timeout=10)
        f.login(user, password)
        return f

    def __exit__(self, *exc):
        self.srv.close_all()
        self.thread.join(5)
        self.tmp.cleanup()
