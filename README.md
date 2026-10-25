# z8-wifi-drop

Send photos from a Nikon Z8 (or Z9) to a PC over Wi-Fi as you shoot, then
rename them per item for eBay / Etsy listings: `ROLEX-116610_01.jpg`, `_02` ...

No Nikon software is needed. The camera's built-in **FTP upload** sends each
shot to a small server on the PC. Works on **Windows** and **Linux**.

**Printable step-by-step guide:** [docs/Z8-WiFi-Setup-Guide.pdf](docs/Z8-WiFi-Setup-Guide.pdf)

## Quick start (Windows)

1. Install Python 3 from [python.org](https://www.python.org/downloads/) (tick **Add python.exe to PATH**).
2. On this page: **Code > Download ZIP**. Unzip it somewhere it can stay, for example `C:\z8-wifi-drop`.
3. Double-click **`Install.bat`** and say **Yes** to the admin prompt.
4. Open **`Z8 camera settings.txt`** on the desktop. Type those values into the camera (see the guide).
5. Shoot. Photos land in `C:\QDAM\Listings\Incoming`.
6. To rename per item: double-click **Z8 Photo Sorter** on the desktop and type the item ID.

## Quick start (Linux)

    git clone https://github.com/xtresno/z8-wifi-drop && cd z8-wifi-drop
    ./install-linux.sh

Photos land in `~/QDAM/Listings/Incoming`.

## What is in here

| File | What it does |
|---|---|
| `server.py` | FTP server on port 2121 (passive 60000-60100). Logs every upload to `events.jsonl`. |
| `watcher.py` | Type an item ID; new JPEGs become `ID_01.jpg`, `ID_02.jpg` ... in `Listings/ID/`. |
| `measure.py` | `ping`: 60 s link test. `timing`: seconds per photo for the next 5 shots. |
| `bench.py` | Benchmark on the PC alone, no camera. |
| `install-windows.ps1`, `Install.bat` | Windows setup: venv, firewall (local network only), start at boot, desktop shortcut. |
| `install-linux.sh` | Linux setup: venv, systemd user service, ufw rules. |
| `tests/` | `cd tests && python -m unittest test_wifi_drop`. CI runs them on Windows and Linux. |

## Measured (Z8 in a garage, router indoors, 2026-10-25)

| | Result |
|---|---|
| Ping, 60 s | 0% loss |
| JPEG Fine (~17 MB) | 1.6 s per photo, ~85 Mbit/s |
| RAW (NEF, ~50 MB) | 4.5 s per photo |

Upload only JPEGs for listings: **Options > Upload RAW + JPEG as > JPEG only**.

## Safety

- The firewall rules allow only the local network.
- The FTP login is made on first install and is different on every PC.
  It is plain FTP: fine inside a home network, never expose the port to the internet.
- The server cannot delete files. A photo cut off mid-upload is never sorted
  (a JPEG must end with its FF D9 marker).

## Need help?

Open [Claude Code](https://claude.com/claude-code) in this folder and describe
the problem. [CLAUDE.md](CLAUDE.md) tells it where the logs are and what
usually goes wrong.

MIT License.
