# Tools, Languages & Environment

Inventory of languages, libraries, IDEs, and runtime pieces used in this project — useful for recruiters and for reproducing the build.

> Related: [README](../README.md) · [Setup](SETUP.md) · [Hardware](HARDWARE.md)

---

## Languages & build


| Piece      | Detail                                                   |
| ---------- | -------------------------------------------------------- |
| **C**      | C11 (`-std=c11`), `gcc`, `-Wall -Wextra -O2`             |
| **Make**   | Top-level `Makefile` → binary `./monitor`                |
| **Link**   | `-lutil` (PTY helpers on Linux)                          |
| **Python** | 3.x via **venv** (`.venv`) — required on Raspberry Pi OS |
| **Shell**  | Bash install scripts under `deploy/`                     |


Primary C modules: `src/monitor.c`, `config.c`, `wifi.c`, `ssh_pty.c`, `parse.c`, `util.c` · headers in `include/monitor.h`.

---

## Python packages

From `requirements.txt`:


| Package | Used for |
|---------|----------|
| `selenium` | Portal + Faxback browser automation |
| `pyotp` | TOTP 2FA for portal login |
| `openpyxl` | Excel helpers / local workbook ops |
| `Office365-REST-Python-Client` | SharePoint / Office 365 REST helpers |
| `msal` | Microsoft auth + token cache |
| `requests` | HTTP utilities |

Browser runtime on Pi (apt, via `make deps`) — **known good on this image**:

| Package | Version |
|---------|---------|
| Chromium | `151.0.7922.173` (Debian 13 / trixie, `1:151.0.7922.173-1~deb13u1+rpt1`) |
| ChromeDriver | `151.0.7922.173` (same build; `chromium-driver` package matched) |

Re-check after OS updates:

```bash
chromium --version || chromium-browser --version
chromedriver --version
dpkg -l chromium chromium-driver 2>/dev/null | awk '/^ii/ {print $2, $3}'
```

Selenium expects Chromium and ChromeDriver **major builds to match** (as above).

---

## Host OS & system tools


| Tool                     | Role                                                                               |
| ------------------------ | ---------------------------------------------------------------------------------- |
| Raspberry Pi OS / Debian | Debian **13 (trixie)** on Pi 4B Rev 1.5 · kernel `6.18.34+rpt-rpi-v8` · **64-bit** |
| NetworkManager / `nmcli` | Join/leave lab & station SSIDs                                                     |
| OpenSSH client           | SSH to routers; jump host                                                          |
| `scp`                    | Optional laptop sync                                                               |
| systemd                  | `simplifi-monitor.timer`, `simplifi-config-web.timer`                              |
| Tailscale                | Pi ↔ US jump laptop overlay                                                        |
| journald                 | `journalctl -u simplifi-*.service`                                                 |


Windows jump laptop:


| Tool | Role |
|------|------|
| Windows | Jump host OS (lab laptop) |
| `netsh wlan` (via collector script) | Station Wi‑Fi hop |
| Python 3 | Runs `us_jump_collect.py` |
| Tailscale | Reachable as `jump_host` |
| OpenSSH | Remote access from the Pi |


---

## Application / product integrations


| System               | Interface                                                               |
| -------------------- | ----------------------------------------------------------------------- |
| Station routers      | SSH + CLI/JSON (`ubus` / modem)                                         |
| Simplifi Portal      | HTTPS + Selenium                                                        |
| Faxback              | HTTPS + Selenium                                                        |
| Microsoft SharePoint | Graph / MSAL Excel cell updates                                         |
| Anydesk              | IDs stored as station metadata (remote desktop not driven by this repo) |


---

## IDE & development workflow


| Tool | How it was used |
|------|-----------------|
| **Cursor** / editor on PC + SSH to Pi | Edit C/Python, iterate on the lab appliance |
| Git | Source control; secrets gitignored |
| Windows laptop | SharePoint / Excel verification; US jump host |


Suggested local layout when developing from a PC:

- Edit in IDE → `git push` → `git pull` on Pi → `make` / restart web  
- Optional helper: `deploy/sync-to-pi.ps1`

---

## Runtime ports & bind addresses


| Service                 | Bind                | Port     |
| ----------------------- | ------------------- | -------- |
| `scripts/config_web.py` | `0.0.0.0` (default) | **8765** |


Override:

```bash
.venv/bin/python3 scripts/config_web.py --bind 0.0.0.0 --port 8765
```

---

## Key scripts (quick map)


| Script                           | Responsibility                                    |
| -------------------------------- | ------------------------------------------------- |
| `scripts/config_web.py`          | Settings UI + Monitor Progress / Summary / Detail |
| `scripts/monitor_progress.py`    | Start/stop monitor, progress + daily summary APIs |
| `scripts/stations_lib.py`        | Shared station conf IO                            |
| `scripts/us_jump_run.py`         | Pi-side US orchestration                          |
| `scripts/us_jump_collect.py`     | Laptop-side collect                               |
| `scripts/portal_logs.py`         | Portal log download                               |
| `scripts/send_fax.py`            | Faxback automation                                |
| `scripts/sharepoint_excel.py` | Excel fill via Graph |
| `scripts/config_wizard.py` | CLI conf editor (alternative to web) |


---

