import ftplib
import io
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import LOGIN, LiveServer, fake_jpeg

import common
import exif
import measure
import watcher


# SO_LINGER on, 0 s: close() sends a reset, like a link that died.
LINGER_RESET = struct.pack("hh" if os.name == "nt" else "ii", 1, 0)


def wait_until(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def events_of(srv, ev):
    return [r for r in common.read_events(srv.events) if r["ev"] == ev]


class TestExif(unittest.TestCase):
    def test_reads_time_subsec_and_offset(self):
        with tempfile.TemporaryDirectory() as d:
            p = fake_jpeg(Path(d) / "a.jpg", "2026:10:24 21:30:05", "25", "-04:00")
            want = datetime(2026, 10, 24, 21, 30, 5, 250000, tzinfo=timezone(timedelta(hours=-4)))
            self.assertEqual(exif.shot_time(p), want)

    def test_no_offset_uses_local_time(self):
        with tempfile.TemporaryDirectory() as d:
            p = fake_jpeg(Path(d) / "a.jpg", offset="")
            self.assertEqual(exif.shot_time(p), datetime(2026, 10, 24, 21, 30, 5, 250000).astimezone())

    def test_not_a_jpeg(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.jpg"
            p.write_bytes(b"hello")
            self.assertIsNone(exif.shot_time(p))
            p.write_bytes(b"\xff\xd8\xff\xe1\x00\x08Exif\0\0")  # truncated
            self.assertIsNone(exif.shot_time(p))


class TestServer(unittest.TestCase):
    def test_upload_lands_and_is_logged(self):
        with LiveServer() as srv, tempfile.TemporaryDirectory() as d:
            src = fake_jpeg(Path(d) / "DSC_0001.JPG", body=2_000_000)
            f = srv.client()
            with open(src, "rb") as fh:
                f.storbinary("STOR DSC_0001.JPG", fh)
            f.quit()
            dest = srv.incoming / "DSC_0001.JPG"
            self.assertEqual(dest.read_bytes(), src.read_bytes())
            self.assertTrue(wait_until(lambda: events_of(srv, "done")))
            rec = events_of(srv, "done")[0]
            self.assertEqual(rec["bytes"], src.stat().st_size)
            self.assertEqual(rec["shot"], round(exif.shot_time(src).timestamp(), 3))
            self.assertEqual(events_of(srv, "login")[0]["ip"], "127.0.0.1")

    def test_wrong_password_rejected(self):
        with LiveServer() as srv:
            with self.assertRaises(ftplib.error_perm):
                srv.client(password="nope")

    def test_cannot_escape_incoming_or_delete(self):
        with LiveServer() as srv:
            f = srv.client()
            f.storbinary("STOR ../../escape.jpg", io.BytesIO(b"x"))
            self.assertFalse((srv.incoming.parent.parent / "escape.jpg").exists())
            self.assertTrue((srv.incoming / "escape.jpg").exists())  # clamped to the root
            with self.assertRaises(ftplib.error_perm):
                f.delete("escape.jpg")
            f.quit()

    def test_camera_style_session(self):
        # What a Z8 is likely to send: TYPE I, PASV, CWD/MKD, SIZE, STOR, MFMT.
        with LiveServer() as srv:
            f = srv.client()
            f.voidcmd("TYPE I")
            f.mkd("DCIM")
            f.cwd("DCIM")
            f.storbinary("STOR DSC_0002.JPG", io.BytesIO(b"\xff\xd8abc"))
            self.assertEqual(f.size("DSC_0002.JPG"), 5)
            f.sendcmd("MFMT 20261024213005 DSC_0002.JPG")
            f.quit()
            self.assertTrue((srv.incoming / "DCIM/DSC_0002.JPG").exists())

    def test_dropped_link_keeps_partial_then_resume_completes(self):
        with LiveServer() as srv:
            data = os.urandom(3_000_000) + b"\xff\xd9"
            f = srv.client()
            conn = f.transfercmd("STOR DSC_0003.JPG")
            conn.sendall(data[:1_000_000])
            # Cut only once the server is writing; a reset before that drops
            # the unread STOR itself, which is a different case.
            part = srv.incoming / "DSC_0003.JPG"
            self.assertTrue(wait_until(lambda: part.exists() and part.stat().st_size > 0))
            # Wi-Fi drops: kill both sockets with no goodbye.
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_RESET)
            conn.close()
            f.sock.close()
            self.assertTrue(wait_until(lambda: events_of(srv, "partial")))
            part = srv.incoming / "DSC_0003.JPG"
            self.assertTrue(part.exists())
            # Camera reconnects and resumes from where it stopped.
            f = srv.client()
            have = f.size("DSC_0003.JPG")
            f.storbinary("STOR DSC_0003.JPG", io.BytesIO(data[have:]), rest=have)
            f.quit()
            self.assertEqual(part.read_bytes(), data)
            self.assertTrue(wait_until(lambda: events_of(srv, "done")))
            self.assertTrue(events_of(srv, "done")[-1]["resumed"])

    def test_clean_close_mid_jpeg_is_partial(self):
        with LiveServer() as srv:
            f = srv.client()
            conn = f.transfercmd("STOR DSC_0004.JPG")
            conn.sendall(b"\xff\xd8" + os.urandom(500_000))
            conn.close()  # FIN, looks like a normal end of file
            f.quit()
            self.assertTrue(wait_until(lambda: events_of(srv, "partial")))
            self.assertEqual(events_of(srv, "done"), [])

    def test_five_parallel_uploads(self):
        with LiveServer() as srv:
            blobs = {f"DSC_{i:04d}.JPG": os.urandom(1_000_000) + b"\xff\xd9" for i in range(10, 15)}

            def send(name, blob):
                f = srv.client()
                f.storbinary(f"STOR {name}", io.BytesIO(blob))
                f.quit()

            threads = [threading.Thread(target=send, args=kv) for kv in blobs.items()]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            for name, blob in blobs.items():
                self.assertEqual((srv.incoming / name).read_bytes(), blob)
            self.assertTrue(wait_until(lambda: len(events_of(srv, "done")) == 5))

    def test_login_file_created_private(self):
        import server
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cfg/login.json"
            a = server.load_login(p)
            self.assertEqual(server.load_login(p), a)
            self.assertEqual(len(a["password"]), 8)
            if os.name != "nt":
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)


class TestWatcher(unittest.TestCase):
    def test_clean_id(self):
        self.assertEqual(watcher.clean_id(" rolex-116610 "), "ROLEX-116610")
        for bad in ["", "../x", "a b", "con", "x" * 41, "-lead", "a/b", "a\\b"]:
            self.assertIsNone(watcher.clean_id(bad), bad)

    def test_ftp_uploads_sorted_in_shot_order(self):
        with LiveServer() as srv, tempfile.TemporaryDirectory() as d:
            # Uploaded out of order; shot times say 0003 came first.
            shots = {"DSC_0005.JPG": "21:30:09", "DSC_0003.JPG": "21:30:01", "DSC_0004.JPG": "21:30:05"}
            f = srv.client()
            for name, t in shots.items():
                src = fake_jpeg(Path(d) / name, f"2026:10:24 {t}")
                with open(src, "rb") as fh:
                    f.storbinary(f"STOR {name}", fh)
            f.quit()
            self.assertTrue(wait_until(lambda: len(events_of(srv, "done")) == 3))
            s = watcher.Sorter(srv.incoming, srv.listings, srv.events)
            moves = s.sort_into("OMEGA")
            got = [(a.name, b.name) for a, b in moves]
            self.assertEqual(got, [("DSC_0003.JPG", "OMEGA_01.jpg"), ("DSC_0004.JPG", "OMEGA_02.jpg"),
                                   ("DSC_0005.JPG", "OMEGA_03.jpg")])
            self.assertEqual(sorted(p.name for p in srv.incoming.iterdir()), [])

    def test_upload_in_progress_is_not_moved(self):
        with LiveServer() as srv:
            f = srv.client()
            conn = f.transfercmd("STOR DSC_0006.JPG")
            conn.sendall(b"\xff\xd8" + os.urandom(500_000))
            self.assertTrue(wait_until(lambda: (srv.incoming / "DSC_0006.JPG").exists()))
            s = watcher.Sorter(srv.incoming, srv.listings, srv.events)
            far_future = time.time() + 3600  # even long after, a "start" blocks it
            self.assertEqual(s.sort_into("X", now=far_future), [])
            conn.sendall(b"\xff\xd9")
            conn.close()
            f.voidresp()
            f.quit()
            self.assertTrue(wait_until(lambda: events_of(srv, "done")))
            self.assertEqual([b.name for _, b in s.sort_into("X")], ["X_01.jpg"])

    def test_partial_is_not_moved(self):
        with LiveServer() as srv:
            f = srv.client()
            conn = f.transfercmd("STOR DSC_0007.JPG")
            conn.sendall(os.urandom(500_000))  # more than the 128 KB write buffer
            part = srv.incoming / "DSC_0007.JPG"
            self.assertTrue(wait_until(lambda: part.exists() and part.stat().st_size > 0))
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, LINGER_RESET)
            conn.close()
            f.sock.close()
            self.assertTrue(wait_until(lambda: events_of(srv, "partial")))
            s = watcher.Sorter(srv.incoming, srv.listings, srv.events)
            self.assertEqual(s.sort_into("X", now=time.time() + 3600), [])

    def test_hand_copied_file_waits_until_settled(self):
        with tempfile.TemporaryDirectory() as d:
            inc, lst, ev = Path(d) / "Incoming", Path(d), Path(d) / "events.jsonl"
            inc.mkdir()
            fake_jpeg(inc / "IMG_1.jpg")
            s = watcher.Sorter(inc, lst, ev)
            now = time.time()
            self.assertEqual(s.sort_into("A", now=now), [])  # first sight
            self.assertEqual(s.sort_into("A", now=now + 1), [])  # not settled yet
            os.utime(inc / "IMG_1.jpg", (now - 60, now - 60))
            self.assertEqual(s.sort_into("A", now=now + 2), [])  # mtime changed: restart clock
            self.assertEqual([b.name for _, b in s.sort_into("A", now=now + 2 + watcher.SETTLE)], ["A_01.jpg"])

    def test_truncated_hand_copy_never_moves(self):
        with tempfile.TemporaryDirectory() as d:
            inc, lst, ev = Path(d) / "Incoming", Path(d), Path(d) / "events.jsonl"
            inc.mkdir()
            p = fake_jpeg(inc / "cut.jpg")
            p.write_bytes(p.read_bytes()[:-50])
            old = time.time() - 60
            os.utime(p, (old, old))
            s = watcher.Sorter(inc, lst, ev)
            s.sort_into("D")
            self.assertEqual(s.sort_into("D", now=time.time() + 600), [])

    def test_numbering_continues_and_skips_other_types(self):
        with tempfile.TemporaryDirectory() as d:
            inc, lst, ev = Path(d) / "Incoming", Path(d), Path(d) / "events.jsonl"
            inc.mkdir()
            (lst / "B").mkdir()
            fake_jpeg(lst / "B/B_01.jpg")
            fake_jpeg(lst / "B/B_07.jpg")
            fake_jpeg(inc / "n.JPEG")
            (inc / "DSC_1.NEF").write_bytes(b"raw")
            old = time.time() - 60
            for p in inc.iterdir():
                os.utime(p, (old, old))
            s = watcher.Sorter(inc, lst, ev)
            s.sort_into("B")
            moves = s.sort_into("B", now=time.time() + watcher.SETTLE)
            self.assertEqual([b.name for _, b in moves], ["B_08.jpg"])
            self.assertTrue((inc / "DSC_1.NEF").exists())

    def test_duplicate_is_set_aside_not_numbered(self):
        with tempfile.TemporaryDirectory() as d:
            inc, lst, ev = Path(d) / "Incoming", Path(d), Path(d) / "events.jsonl"
            inc.mkdir()
            (lst / "C").mkdir()
            fake_jpeg(lst / "C/C_01.jpg", seed=3)
            fake_jpeg(inc / "DSC_9.JPG", seed=3)  # same bytes, sent twice
            old = time.time() - 60
            os.utime(inc / "DSC_9.JPG", (old, old))
            s = watcher.Sorter(inc, lst, ev)
            s.sort_into("C")
            moves = s.sort_into("C", now=time.time() + watcher.SETTLE)
            self.assertEqual(moves[0][1], inc / watcher.DUPES / "DSC_9.JPG")
            self.assertEqual(sorted(p.name for p in (lst / "C").iterdir()), ["C_01.jpg"])

    def test_event_tail_handles_torn_line_and_reset(self):
        with tempfile.TemporaryDirectory() as d:
            ev = Path(d) / "events.jsonl"
            ev.write_text('{"ev": "done", "path": "/a", "bytes": 1}\n{"ev": "start", "pa')
            t = watcher.EventTail(ev)
            self.assertEqual(list(t.update()), [os.path.realpath("/a")])
            with open(ev, "a") as f:
                f.write('th": "/b"}\n')
            self.assertEqual(t.update()[os.path.realpath("/b")]["ev"], "start")
            ev.write_text("")
            t.update()
            self.assertEqual(t.pos, 0)


class TestMeasure(unittest.TestCase):
    LINUX = ("60 packets transmitted, 59 received, 1.66667% packet loss, time 59081ms\n"
             "rtt min/avg/max/mdev = 1.912/4.204/38.551/5.002 ms\n")
    WINDOWS = ("    Packets: Sent = 60, Received = 60, Lost = 0 (0% loss),\n"
               "    Minimum = 2ms, Maximum = 41ms, Average = 5ms\n")

    def test_parse_ping(self):
        self.assertEqual(measure.parse_ping(self.LINUX), {"loss": 1.66667, "rtt": (1.912, 4.204, 38.551)})
        self.assertEqual(measure.parse_ping(self.WINDOWS), {"loss": 0.0, "rtt": (2.0, 5.0, 41.0)})
        self.assertEqual(measure.parse_ping("100% packet loss"), {"loss": 100.0, "rtt": None})

    def test_timing_and_verdict(self):
        recs = [{"file": f"DSC_{i}.JPG", "bytes": 20_000_000, "secs": 3.0, "mbps": 53.3,
                 "t": 1000.0 + i * 10, "shot": 996.0 + i * 10} for i in range(5)]
        rep = measure.timing_report(recs)
        self.assertTrue(rep["clock_ok"])
        self.assertAlmostEqual(rep["avg_delay"], 4.0)
        self.assertIsNone(measure.verdict(loss=1.0, secs=rep["avg_delay"]))
        # Camera clock 1 hour off: delay is unknown, fall back to transfer time.
        skew = [{**r, "shot": r["shot"] + 3600} for r in recs]
        self.assertFalse(measure.timing_report(skew)["clock_ok"])
        self.assertEqual(measure.timing_report(skew, clock_offset=3600)["avg_delay"], 4.0)
        self.assertIn("bottleneck", measure.verdict(loss=3.0))
        self.assertIn("bottleneck", measure.verdict(secs=6.0))

    def test_hour_offset_found(self):
        # Real case from 2026-10-25: the Z8 clock was one hour ahead.
        recs = [{"file": f"DSC_{i}.JPG", "bytes": 17_000_000, "secs": 1.6, "mbps": 85.0,
                 "t": 1000.0 + i * 10, "shot": 996.0 + i * 10 + 3600} for i in range(5)]
        self.assertEqual(measure.hour_offset(recs), 3600)
        self.assertAlmostEqual(measure.timing_report(recs, 3600)["avg_delay"], 4.0)
        ok = [{"t": 1000.0, "shot": 996.0}]
        self.assertEqual(measure.hour_offset(ok), 0)
        self.assertEqual(measure.hour_offset([{"t": 1000.0}]), 0)
        messy = [{"t": 1000.0, "shot": 1000.0 + 1800}]  # half an hour: not a DST slip
        self.assertEqual(measure.hour_offset(messy), 0)

    def test_real_z8_log_backlog(self):
        # 2026-10-25, RAW + JPEG upload: clock 1 h + a few s ahead, queue growing.
        delays = [-3600.4, -3595.1, -3592.3, -3588.6, -3584.9]
        recs = [{"file": f"DSC_{i}.JPG", "bytes": 17_000_000, "secs": 1.6, "mbps": 85.0,
                 "t": 1000.0 + i * 6, "shot": 1000.0 + i * 6 - d} for i, d in enumerate(delays)]
        self.assertEqual(measure.hour_offset(recs), 3600)
        rep = measure.timing_report(recs, 3600)
        self.assertFalse(rep["clock_ok"])  # still seconds off: no absolute delay
        self.assertAlmostEqual(rep["backlog"], 15.5)

    def test_camera_ip_from_last_login(self):
        with tempfile.TemporaryDirectory() as d:
            ev = Path(d) / "e.jsonl"
            common.log_event(ev, "login", ip="192.168.1.50", user="z8")
            common.log_event(ev, "login", ip="192.168.1.61", user="z8")
            self.assertEqual(measure.camera_ip(ev), "192.168.1.61")


if __name__ == "__main__":
    unittest.main()
