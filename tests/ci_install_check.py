"""After install-windows.ps1 (or install-linux.sh): act like the camera.

Logs in to the installed server on 127.0.0.1:2121 with the generated login,
uploads a fake Z8 JPEG, checks it landed whole, then lets the watcher file it.
Exit code 0 = the installed system works end to end.
"""
import ftplib
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common  # noqa: E402
import watcher  # noqa: E402
from helpers import fake_jpeg  # noqa: E402


def main():
    login = json.loads(common.LOGIN.read_text())
    with tempfile.TemporaryDirectory() as d:
        src = fake_jpeg(Path(d) / "DSC_0001.JPG", body=5_000_000, seed=9)
        f = ftplib.FTP()
        f.connect("127.0.0.1", common.PORT, timeout=15)
        f.login(login["user"], login["password"])
        with open(src, "rb") as fh:
            f.storbinary("STOR DSC_0001.JPG", fh)
        f.quit()
        landed = common.INCOMING / "DSC_0001.JPG"
        assert landed.read_bytes() == src.read_bytes(), "upload changed on the way"
        print(f"OK upload landed whole: {landed}")

        end = time.time() + 10
        while time.time() < end and not any(
                r["ev"] == "done" and r["file"] == "DSC_0001.JPG" for r in common.read_events(common.EVENTS)):
            time.sleep(0.2)
        moves = watcher.Sorter(common.INCOMING, common.LISTINGS, common.EVENTS).sort_into("CI-TEST")
        dest = common.LISTINGS / "CI-TEST" / "CI-TEST_01.jpg"
        assert [b for _, b in moves] == [dest], moves
        assert dest.read_bytes() == src.read_bytes()
        print(f"OK watcher filed it as {dest}")


if __name__ == "__main__":
    main()
