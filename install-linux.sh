#!/usr/bin/env bash
# Linux setup for the Z8 Wi-Fi FTP drop box (systemd user service + ufw).
#   ./install-linux.sh             install and start
#   ./install-linux.sh --uninstall stop and remove the service (photos are kept)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT=z8-wifi-drop.service
UNIT_PATH="$HOME/.config/systemd/user/$UNIT"

if [ "${1:-}" = "--uninstall" ]; then
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_PATH"
    systemctl --user daemon-reload
    echo "Service removed. Firewall rules stay; see them with: sudo ufw status numbered"
    exit 0
fi

python3 -m venv "$HERE/.venv"
"$HERE/.venv/bin/pip" install -q --disable-pip-version-check pyftpdlib

mkdir -p "$(dirname "$UNIT_PATH")"
cat > "$UNIT_PATH" <<UNIT_EOF
[Unit]
Description=Z8 Wi-Fi FTP drop box

[Service]
ExecStart=$HERE/.venv/bin/python -u $HERE/server.py
Restart=on-failure
MemoryMax=256M

[Install]
WantedBy=default.target
UNIT_EOF
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT"

# Firewall: only the home subnet may reach the server.
DEV=$(ip -4 route show default | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')
SUBNET=$(ip -4 route show dev "$DEV" proto kernel scope link | awk '{print $1; exit}')
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow from "$SUBNET" to any port 2121 proto tcp comment 'Z8 Wi-Fi FTP'
    sudo ufw allow from "$SUBNET" to any port 60000:60100 proto tcp comment 'Z8 FTP passive'
else
    echo "ufw is not active. If another firewall runs, open TCP 2121 and 60000-60100 for $SUBNET."
fi

if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo "Note: the server only runs while you are logged in. To start it at boot: sudo loginctl enable-linger $USER"
fi

LOGIN="$HOME/.config/z8-wifi-drop/login.json"
for _ in $(seq 20); do [ -f "$LOGIN" ] && break; sleep 0.5; done
IP=$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')
"$HERE/.venv/bin/python" - "$LOGIN" "$IP" "$HERE" <<'PY_EOF'
import json, sys
login = json.load(open(sys.argv[1]))
print(f"""
Z8 camera settings for this PC
  FTP > Server type: FTP     Address: {sys.argv[2]}     Port: 2121
  FTP > PASV mode: ON        Anonymous login: OFF
  FTP > User ID: {login['user']}       Password: {login['password']}
Photos land in ~/QDAM/Listings/Incoming
Photo sorter: {sys.argv[3]}/.venv/bin/python {sys.argv[3]}/watcher.py
""")
PY_EOF
