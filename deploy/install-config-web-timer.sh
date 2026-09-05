#!/bin/bash
# Install systemd timer: start config_web.py at 08:00, auto-kill after 3h (~11:00).
# Usage on the Pi, from the repo:
#   chmod +x deploy/install-config-web-timer.sh
#   sudo bash deploy/install-config-web-timer.sh
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-${USER:-pi}}"
UNIT_DIR=/etc/systemd/system
VENV_PY="$ROOT/.venv/bin/python3"

if [ ! -x "$VENV_PY" ]; then
    echo "Missing venv python: $VENV_PY" >&2
    echo "Create it first, e.g. python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

install -d "$UNIT_DIR"
sed -e "s|/home/pi/Workspace/Daily_Monitor/automate-daily_monitor|$ROOT|g" \
    -e "s|^User=pi|User=$USER_NAME|" \
    -e "s|^Group=pi|Group=$USER_NAME|" \
    "$ROOT/deploy/simplifi-config-web.service" > "$UNIT_DIR/simplifi-config-web.service"

# Unit files copied from Windows may have CRLF; systemd rejects that.
sed -i 's/\r$//' "$UNIT_DIR/simplifi-config-web.service"
sed -i 's/\r$//' "$ROOT/deploy/simplifi-config-web.timer"
install -m 0644 "$ROOT/deploy/simplifi-config-web.timer" "$UNIT_DIR/simplifi-config-web.timer"
sed -i 's/\r$//' "$UNIT_DIR/simplifi-config-web.timer"

systemctl daemon-reload
systemctl enable --now simplifi-config-web.timer
echo
systemctl list-timers simplifi-config-web.timer --no-pager
echo
echo "Timezone: $(timedatectl show -p Timezone --value 2>/dev/null || date +%Z)"
echo "Set Vietnam time if needed: sudo timedatectl set-timezone Asia/Ho_Chi_Minh"
echo
echo "Open UI (while running): http://<pi-ip>:8765"
echo "Start now (test):        sudo systemctl start simplifi-config-web.service"
echo "Stop now:                sudo systemctl stop simplifi-config-web.service"
echo "Status:                  systemctl status simplifi-config-web.service"
echo "Logs:                    journalctl -u simplifi-config-web.service -e"
echo "Next timer:              systemctl list-timers simplifi-config-web.timer"
