#!/bin/bash
# Install systemd timer: run ./monitor every day at 08:00 (Pi local time).
# Usage on the Pi, from the repo:
#   chmod +x deploy/install-timer.sh
#   sudo bash deploy/install-timer.sh
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-${USER:-pi}}"
UNIT_DIR=/etc/systemd/system

if [ ! -x "$ROOT/monitor" ]; then
    echo "Build first: cd $ROOT && make" >&2
    exit 1
fi

install -d "$UNIT_DIR"
sed -e "s|/home/pi/Workspace/Daily_Monitor/automate-daily_monitor|$ROOT|g" \
    -e "s|^User=pi|User=$USER_NAME|" \
    -e "s|^Group=pi|Group=$USER_NAME|" \
    "$ROOT/deploy/simplifi-monitor.service" > "$UNIT_DIR/simplifi-monitor.service"
# Unit files copied from Windows may have CRLF; systemd rejects that.
sed -i 's/\r$//' "$UNIT_DIR/simplifi-monitor.service"
sed -i 's/\r$//' "$ROOT/deploy/simplifi-monitor.timer"
install -m 0644 "$ROOT/deploy/simplifi-monitor.timer" "$UNIT_DIR/simplifi-monitor.timer"
sed -i 's/\r$//' "$UNIT_DIR/simplifi-monitor.timer"

systemctl daemon-reload
systemctl enable --now simplifi-monitor.timer
echo
systemctl list-timers simplifi-monitor.timer --no-pager
echo
echo "Timezone: $(timedatectl show -p Timezone --value 2>/dev/null || date +%Z)"
echo "Set Vietnam time if needed: sudo timedatectl set-timezone Asia/Ho_Chi_Minh"
echo "Next run: systemctl list-timers simplifi-monitor.timer"
echo "Logs:     journalctl -u simplifi-monitor.service -e"
echo "Test now: sudo systemctl start simplifi-monitor.service"
