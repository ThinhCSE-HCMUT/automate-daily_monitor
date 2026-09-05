# Hardware & Network

Physical and network context for the daily monitor.

> Related: [Architecture](ARCHITECTURE.md) · [Setup](SETUP.md) · [Tools](TOOLS.md)

---

## Raspberry Pi (orchestrator)


| Item         | Value                                                               |
| ------------ | ------------------------------------------------------------------- |
| Role         | Runs `./monitor`, Python stages, optional `config_web.py`           |
| Hostname     | `ast-station`                                                       |
| Board        | **Raspberry Pi 4 Model B Rev 1.5**                                  |
| CPU          | 4× ARM Cortex-A72 @ up to 1.8 GHz (`aarch64`)                       |
| RAM          | ~1.8 GiB                                                            |
| OS           | Debian GNU/Linux **13 (trixie)** · 64-bit                           |
| Kernel       | `6.18.34+rpt-rpi-v8` (Raspberry Pi kernel package)                  |
| Wi‑Fi iface  | Default `wlan0` (`wifi_iface` in conf)                              |
| Typical path | `/home/pi/Workspace/Daily_Monitor/automate-daily_monitor`           |
| LAN access   | DHCP — use `hostname -I` (also has Tailscale IPv4/IPv6 when online) |
| Config UI    | `http://<pi-ip>:8765`                                               |


### Discover Pi hardware (SSH into the Pi)

```bash
# Board model
cat /proc/device-tree/model; echo

# OS / kernel / arch
cat /etc/os-release
uname -a
getconf LONG_BIT

# CPU / RAM / storage
lscpu
free -h
df -h /

# Network
hostname -I
ip -br a
nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status
```

### Why a Pi fits this job

- Always-on lab appliance with Wi‑Fi client capability (critical for **SSID hopping**)
- Enough headroom for Chromium/Selenium portal/fax stages
- Easy `systemd` scheduling and Tailscale membership

---

## Station routers (CPE)

Lab stations run **Simplifi** OpenWrt-derived firmware on **BS6425L** hardware (NXP Layerscape / `fsl,ls1012a-rdb` board family) with a **Quectel EC25**-class cellular modem (`simcom` CLI + `ubus call cellular status`).


| Station type | Count | How the Pi reaches them                       | Notes                                |
| ------------ | ----- | --------------------------------------------- | ------------------------------------ |
| Voicelink    | 2     | Join station Wi‑Fi → SSH gateway              | Desk phone / voice call = **manual** |
| Fax          | 2     | Same Wi‑Fi + SSH; Faxback for fax verdict     | Fax queue user mapped in conf        |
| Virtual      | 2     | **US Windows jump laptop** hops Virtual Wi‑Fi | `access=tailscale` on Pi side        |


### Confirmed platform (lab sample)

Sample taken from a live station (`ubus call system board` / `simcom`):


| Field                                        | Value                                                |
| -------------------------------------------- | ---------------------------------------------------- |
| Product / model                              | **BS6425L**                                          |
| Board name                                   | `fsl,ls1012a-rdb`                                    |
| CPU                                          | ARMv8 / `aarch64`                                    |
| OS distribution                              | Simplifi (OpenWrt-style)                             |
| OS release                                   | `7.9.2-eval-v7.9.3.2606061336`                       |
| Kernel                                       | `5.4.188`                                            |
| Target                                       | `simplifi_layerscape/generic`                        |
| Modem (ubus `firmware`)                      | `EC25AFFAR07A13M4G` (EC25 family)                    |
| App firmware (`simcom get firmware_version`) | `7.10.0` ← value written to CSV **Firmware Version** |


> All **six** lab stations use the same CPE platform: **BS6425L** + Simplifi OS / EC25 modem family (values above).


| Station | Router model | OS / modem |
|---------|--------------|------------|
| Voicelink 1–2 | BS6425L | Same as confirmed platform |
| Fax 1–2 | BS6425L | Same as confirmed platform |
| Virtual 1–2 | BS6425L | Same as confirmed platform |


### Identity fields (per station)

Configured under `router.N.*` in `monitor.conf` (also editable in the web UI):

- Name, IMEI, Anydesk ID  
- Station Wi‑Fi SSID / password (VN)  
- Access mode: `wifi` vs `tailscale`  
- SharePoint sheet name / status header  
- Fax queue user (Fax only)

### Network addressing (defaults in software)


| Setting | Default | Meaning |
|---------|---------|---------|
| `router_gateway` | `192.168.2.1` | Default SSH gateway on the station’s own Wi‑Fi LAN |
| `router_gateway_alt` | `192.168.10.1` | Fallback gateway if the primary does not answer |
| `ssh_user` | `root` | SSH login |

These are **private RFC1918 addresses on the router’s AP network** (same idea as a home router’s `192.168.1.1`). They are already in `monitor.conf.example` and are **not** company secrets like passwords, IMEIs, or portal URLs. Safe to document.

### Discover router hardware (SSH into the CPE)

After joining the station Wi‑Fi (or from a host that can reach `<IP-Router>`):

```bash
ssh root@<IP-Router>
```

Then on the router:

```bash
ubus call system board
cat /tmp/sysinfo/model
cat /etc/openwrt_release
uname -a
ubus call cellular status
simcom get firmware_version
```

Do **not** paste live MSISDN / IMSI into public docs.

---

## Lab Wi‑Fi (Vietnam)

The Pi must **return** to lab Wi‑Fi after each station hop so later stages (portal, Tailscale, SharePoint) work.

| Setting | Value (live on Pi) | Notes |
|---------|-------------------|--------|
| `lab_ssid_5g` | `Simplifi Lab 5Ghz` | Preferred (exact spelling from `nmcli`) |
| `lab_ssid_24g` | `Simplifi Lab 2.4Ghz` | Fallback |
| `lab_password` | *(secret)* | gitignored conf only |

Other Wi‑Fi profiles seen on the Pi (for human / station access; passwords stay private):

| SSID / connection | Role |
|-------------------|------|
| `Simplifi Lab 5Ghz` | Lab return SSID (5 GHz) |
| `Simplifi Lab 2.4Ghz` | Lab return SSID (2.4 GHz) |
| `Simplifi Office` | Office LAN access to the Pi |
| `Simplifi-3188`, `Simplifi-7513`, … | Station APs (Voicelink / Fax / …) |

To re-check later:

```bash
nmcli -t -f NAME,TYPE connection show
nmcli -t -f SSID device wifi list | head
```

---

## US path — jump laptop

Virtual stations are not collected by hopping from the Pi’s VN Wi‑Fi radio.


| Item         | Value                                                                      |
| ------------ | -------------------------------------------------------------------------- |
| Role         | Join each Virtual station SSID, SSH gateway, return JSON results           |
| OS           | Windows (Win 10 build)                                                     |
| Reachability | Same **Tailscale** tailnet as the Pi                                       |
| Conf keys    | `jump_host`, `jump_user`, `jump_restore_ssid`, `jump_wait_sec`             |
| Example host | Tailscale IP in `monitor.conf.example` — treat as **environment-specific** |
| Scripts      | Pi: `scripts/us_jump_run.py` · Laptop: `scripts/us_jump_collect.py`        |


---

## External services (logical “hardware” of the cloud edge)


| Service                                     | Used for                         |
| ------------------------------------------- | -------------------------------- |
| Simplifi Portal (often STG URL in examples) | Download per-IMEI device logs    |
| Faxback                                     | Send/verify fax for Fax stations |
| Microsoft 365 / SharePoint                  | Daily Excel monitoring workbook  |


URLs and tenants stay in `portal.conf` / `fax.conf` / `sharepoint.conf` — do not paste production secrets into this doc.

---

## Topology sketch

```text
                 ┌──────────────────────┐
                 │   Raspberry Pi       │
                 │  ./monitor + Python  │
                 └─────────┬────────────┘
            lab Wi‑Fi       │       Tailscale
         ┌──────────┐       │       ┌─────────────────┐
         │ Lab APs  │◄──────┘       │ US jump laptop  │
         └──────────┘               └────────┬────────┘
               │                              │
     station APs (VN)                  Virtual APs (US)
               │                              │
         Voicelink / Fax                   Virtual ×2
           gateways                         gateways
```

---

## Safety / lab notes

- SSID hopping **interrupts** the Pi’s normal LAN until lab Wi‑Fi is restored — keep stages ordered as in [ARCHITECTURE.md](ARCHITECTURE.md).
- Never commit live Wi‑Fi PSKs — keep them only in `monitor.conf` on the Pi (`chmod 600`).

