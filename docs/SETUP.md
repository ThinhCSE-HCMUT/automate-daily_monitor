# Setup & Reproduction Guide

Step-by-step to **build**, **configure**, **run**, and **schedule** the daily monitor on a Raspberry Pi.

> Related: [README](../README.md) · [Architecture](ARCHITECTURE.md) · [Hardware](HARDWARE.md) · [Tools](TOOLS.md)

---

## 0. Prerequisites


| Need                                                  | Notes                                                      |
| ----------------------------------------------------- | ---------------------------------------------------------- |
| Raspberry Pi on lab network                           | See [HARDWARE.md](HARDWARE.md)                             |
| Git checkout of this repo                             | `https://github.com/ThinhCSE-HCMUT/automate-daily_monitor` |
| `sudo` for apt / systemd                              |                                                            |
| Station Wi‑Fi PSKs, portal/fax/SharePoint credentials | Keep out of git                                            |
| Tailscale on Pi **and** US jump laptop                | For Virtual stations                                       |
| Timezone                                              | Prefer `Asia/Ho_Chi_Minh` for 08:00 local                  |


```bash
sudo timedatectl set-timezone Asia/Ho_Chi_Minh
timedatectl
```

---



## 1. Clone & packages

```bash
cd ~/Workspace/Daily_Monitor
git clone https://github.com/ThinhCSE-HCMUT/automate-daily_monitor automate-daily_monitor
cd automate-daily_monitor

sudo apt-get update
sudo apt-get install -y build-essential network-manager openssh-client \
  python3 python3-venv python3-pip chromium chromium-driver
```

`NetworkManager` + `nmcli` are required for Wi‑Fi hopping.

---



## 2. Build C monitor + Python venv

```bash
make          # → ./monitor
make deps     # → .venv + pip install -r requirements.txt
```

Verify:

```bash
./monitor -h || true
.venv/bin/python3 -c "import selenium, msal, pyotp; print('ok')"
```

---



## 3. Configuration files

Copy examples (names may already exist on a working Pi):

```bash
cp -n monitor.conf.example monitor.conf
cp -n portal.conf.example portal.conf
cp -n fax.conf.example fax.conf
cp -n sharepoint.conf.example sharepoint.conf
chmod 600 monitor.conf portal.conf fax.conf sharepoint.conf
```

Templates ship as `*.conf.example` in the repo; live `*.conf` files stay gitignored.

### What to fill


| File              | Must set                                                                        |
| ----------------- | ------------------------------------------------------------------------------- |
| `monitor.conf`    | Lab SSIDs/password, each `router.N.*`, `jump_host` / `jump_user`, feature flags |
| `portal.conf`     | email, password, `totp_secret`, `portal_url`                                    |
| `fax.conf`        | Faxback login, numbers, users↔IMEI, attachment                                  |
| `sharepoint.conf` | M365 user, tenant, client_id, site/file URLs                                    |


**Do not commit** filled confs, `token_cache.bin`, or real PSKs.

### Feature flags (`monitor.conf`)


| Key                | `1` means            |
| ------------------ | -------------------- |
| `portal_logs`      | Download portal logs |
| `fax_send`         | Faxback stage        |
| `laptop_sync`      | scp to lab laptop    |
| `sharepoint_excel` | Graph Excel fill     |


---



## 4. First manual run

```bash
cd /home/pi/Workspace/Daily_Monitor/automate-daily_monitor
./monitor -c monitor.conf
```

Watch:

```bash
tail -f output/monitor.log
# or
cat output/monitor_status.json
```

Expect CSV growth:

```bash
head output/daily_monitor.csv
```

---



## 5. Config web UI (manual)

```bash
.venv/bin/python3 scripts/config_web.py --bind 0.0.0.0 --port 8765
```

Open from a lab PC: `http://<pi-ip>:8765`  
Find Pi IP: `hostname -I`

Useful tabs: **Stations**, **VN / US Network**, **Monitor Progress** (Start/Stop + Daily Summary + Detail history).

Stop manually: `Ctrl+C`, or if started via systemd — see below.

---



## 6. systemd — daily `./monitor` at 08:00

```bash
chmod +x deploy/install-timer.sh
sudo bash deploy/install-timer.sh
```

Checks:

```bash
systemctl list-timers simplifi-monitor.timer --no-pager
systemctl status simplifi-monitor.timer --no-pager
journalctl -u simplifi-monitor.service -e
```

Test once now:

```bash
sudo systemctl start simplifi-monitor.service
```

---



## 7. systemd — config web 08:00 → ~11:00

```bash
chmod +x deploy/install-config-web-timer.sh
sudo bash deploy/install-config-web-timer.sh
```

Behavior:

- Timer starts `simplifi-config-web.service` at **08:00**
- `RuntimeMaxSec=3h` stops the UI around **11:00**

Checks:

```bash
systemctl list-timers 'simplifi-*' --all
systemctl is-enabled simplifi-config-web.timer
```


| Goal                                         | Command                                                  |
| -------------------------------------------- | -------------------------------------------------------- |
| Stop web **now**, keep tomorrow’s auto-start | `sudo systemctl stop simplifi-config-web.service`        |
| Start web now (test)                         | `sudo systemctl start simplifi-config-web.service`       |
| Disable schedule entirely                    | `sudo systemctl disable --now simplifi-config-web.timer` |
| Logs                                         | `journalctl -u simplifi-config-web.service -e`           |


---



## 8. US jump laptop (Virtual stations)

High-level checklist (exact Windows paths depend on the jump laptop image):

1. Install Tailscale; set `jump_host` / `jump_user` in `monitor.conf` to that machine
2. Install Python 3 and run/sync `scripts/us_jump_collect.py` as expected by `us_jump_run.py`
3. Ensure the Pi can SSH to the jump host
4. Fill Virtual station `router.N.ssid` / `password` in `monitor.conf`
5. Dry-run from the Pi when ready: `.venv/bin/python3 scripts/us_jump_run.py --conf monitor.conf`

---



## 9. Troubleshooting cheatsheet


| Symptom                   | Things to check                                              |
| ------------------------- | ------------------------------------------------------------ |
| Cannot join station Wi‑Fi | SSID/PSK, `nmcli` radio, iface name                          |
| SSH timeout               | Gateway IP, router up, known_hosts wipe behavior             |
| Portal login fails        | TOTP clock skew, STG URL, Chromium/driver match              |
| US stage skipped / empty  | `jump_host` empty? Tailscale down? Jump script errors in log |
| SharePoint fail / lock    | Prefer Graph fill; Excel open locks; auth cache              |
| Timer wrong hour          | `timedatectl` timezone                                       |
| UI not reachable          | Service running? Port 8765? Correct LAN IP for current SSID? |
| `make deps` / pip blocked | Use project `.venv` (Pi OS may block system pip)             |


---



## Quick command index

```bash
make && make deps
./monitor -c monitor.conf
.venv/bin/python3 scripts/config_web.py --bind 0.0.0.0 --port 8765

sudo bash deploy/install-timer.sh
sudo bash deploy/install-config-web-timer.sh

systemctl list-timers 'simplifi-*' --all
sudo systemctl stop simplifi-config-web.service
sudo systemctl start simplifi-monitor.service
```

