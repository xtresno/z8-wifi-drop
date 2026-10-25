# z8-wifi-drop: notes for Claude Code

The user is probably standing at a camera and is not a programmer. Give one
step at a time, with exact menu names, and ask for a photo of the camera
screen when unsure. Never ask them to read code.

## How it works

Nikon Z8 (FTP upload over Wi-Fi) -> `server.py` (pyftpdlib, port 2121,
passive 60000-60100) -> `Listings/Incoming/` -> `watcher.py` renames to
`ID_##.jpg` in `Listings/ID/`.

| | Windows | Linux |
|---|---|---|
| Photos | `C:\QDAM\Listings\Incoming` | `~/QDAM/Listings/Incoming` |
| Login (user `z8`) | `%USERPROFILE%\AppData\Roaming\z8-wifi-drop\login.json` | `~/.config/z8-wifi-drop/login.json` |
| Event log | `...\z8-wifi-drop\events.jsonl` | `~/.local/state/z8-wifi-drop/events.jsonl` |
| Server log | `...\z8-wifi-drop\server.log` | `journalctl --user -u z8-wifi-drop` |
| Runs as | scheduled task "Z8 Wi-Fi Drop" | systemd user service `z8-wifi-drop` |

`Z8_DROP_LISTINGS` moves the photo folders.

## Debug order

1. Is the server running? Windows: `Get-ScheduledTask "Z8 Wi-Fi Drop"`
   (State Running). Linux: `systemctl --user status z8-wifi-drop`.
2. Did the camera reach it? Read the last lines of `events.jsonl` and the
   server log. "failed login" = wrong login on the camera; nothing at all =
   network, address, port or firewall.
3. Camera IP: the `ip` in the last `login` event. Then
   `python measure.py ping` (loss > 2% = weak Wi-Fi where the camera is).
4. Speed: `python measure.py timing`, then take 5 shots.

## Problems already seen on a real Z8

- **Anonymous login defaults to ON.** The log shows `USER 'anonymous' failed
  login`. Fix: FTP > Anonymous login OFF, User ID `z8`, password from login.json.
- **Port defaults to 21.** It must be 2121.
- **To edit a saved profile, highlight it and press RIGHT**, not OK. Editing
  drops the Wi-Fi link; reconnect from Connect to FTP server.
- **A stray second profile (WLAN2)** can be the active one; the active one has
  the yellow outline in Network settings.
- **RAW + JPEG uploads the 50 MB NEF first** (about 4.5 s), so photos queue.
  Fix: Options > Upload RAW + JPEG as > JPEG only.
- **Camera clock 1 h off (daylight saving)** breaks shutter-to-PC timing.
  `measure.py` detects whole-hour offsets; fix on the camera: Setup menu >
  Time zone and date.
- **The PC's IP can change** (DHCP), then the camera cannot find it. Fix:
  a DHCP reservation for the PC in the router, then re-check the camera Address.
- The camera holds only one network link type at a time (computer, FTP or
  phone app). End the others.

## Rules for changes

- Run `cd tests && python -m unittest test_wifi_drop` before any commit.
- Never commit `login.json`, photos, or `events.jsonl`.
- Never widen the firewall rules past the local subnet.
