# z8-wifi-drop

**Take a photo with your Nikon Z8. About 2 seconds later it is on your PC.**
No cable, no Nikon software.

Optionally, type a name like `LAMP-042` and every new photo is renamed
`LAMP-042_01.jpg`, `LAMP-042_02.jpg` ... into its own folder.

How: the camera has FTP upload built in. This project is the small program
on the PC that receives the photos. Works on **Windows** and **Linux**.

**Printable step-by-step guide (with camera screen photos):**
[docs/Z8-WiFi-Setup-Guide.pdf](docs/Z8-WiFi-Setup-Guide.pdf)

## Good for

- **Product photos:** shoot items in a photo booth or garage, and each item gets its own folder of numbered photos.
- **Studio work:** check focus and lighting on a big screen in another room while you shoot.
- **Inventory and records:** photograph parts, artwork, or collections with an ID per piece.
- **Real estate, interiors, events:** photos are backed up to the PC as you go.
- **Two people, one camera:** one person shoots, another edits upstairs at the same time.

## Quick start (Windows)

1. Install Python 3 from [python.org](https://www.python.org/downloads/) (tick **Add python.exe to PATH**).
2. On this page: **Code > Download ZIP**. Unzip it somewhere it can stay, for example `C:\z8-wifi-drop`.
3. Double-click **`Install.bat`** and say **Yes** to the admin prompt.
4. Open **`Z8 camera settings.txt`** on the desktop. Type those values into the camera (see the guide).
5. Shoot. Photos land in `C:\QDAM\Listings\Incoming`.
6. Optional, to rename per item: double-click **Z8 Photo Sorter** on the desktop and type a name like `LAMP-042`.

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

## Measured speed (Z8 in a garage, router indoors)

| | Result |
|---|---|
| Ping, 60 s | 0% loss |
| JPEG Fine (~17 MB) | 1.6 s per photo, ~85 Mbit/s |
| RAW (NEF, ~50 MB) | 4.5 s per photo |

For speed, upload JPEGs only: **Options > Upload RAW + JPEG as > JPEG only**.

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
