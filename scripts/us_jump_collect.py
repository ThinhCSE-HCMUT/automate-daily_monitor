#!/usr/bin/env python3
"""
Runs ON the US Windows jump laptop (not on the Pi).

Joins each Virtual Station WiFi, SSH to 192.168.2.1, collects fields, then
reconnects the original WiFi so Tailscale comes back.

  py -3 us_jump_collect.py --job job.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from xml.sax.saxutils import escape

_log_fp = None


def log(level: str, msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] [{level}] {msg}"
    print(line, flush=True)
    if _log_fp:
        _log_fp.write(line + "\n")
        _log_fp.flush()


def run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def current_ssid() -> str:
    r = run(["netsh", "wlan", "show", "interfaces"], timeout=20)
    m = re.search(r"^\s*SSID\s*:\s*(.+)$", r.stdout or "", re.M)
    if not m:
        return ""
    ssid = m.group(1).strip()
    if ssid.lower() in ("", "bssid"):
        return ""
    return ssid


def ping_ok(host: str) -> bool:
    r = run(["ping", "-n", "1", "-w", "1000", host], timeout=8)
    return r.returncode == 0


def wait_ping(host: str, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ping_ok(host):
            return True
        time.sleep(1)
    return False


def wlan_profile_xml(ssid: str, password: str) -> str:
    name = escape(ssid)
    key = escape(password)
    return f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{name}</name>
    <SSIDConfig>
        <SSID>
            <name>{name}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{key}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>
"""


def wifi_connect(ssid: str, password: str, timeout: int) -> bool:
    if not ssid:
        return False
    fd, path = tempfile.mkstemp(suffix=".xml", prefix="simplifi-wlan-")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(wlan_profile_xml(ssid, password))
        run(["netsh", "wlan", "add", "profile", f"filename={path}", "user=current"], timeout=30)
        run(["netsh", "wlan", "disconnect"], timeout=20)
        time.sleep(2)
        r = run(["netsh", "wlan", "connect", f"name={ssid}"], timeout=30)
        if r.returncode != 0:
            log("WARN", f"netsh connect {ssid}: {(r.stderr or r.stdout or '').strip()[:200]}")
        return wait_ping("192.168.2.1", timeout) or wait_ping("192.168.10.1", max(8, timeout // 3))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def wifi_restore(ssid: str, timeout: int) -> None:
    if not ssid:
        log("WARN", "No original SSID to restore")
        return
    log("INFO", f"Restore WiFi {ssid}")
    run(["netsh", "wlan", "disconnect"], timeout=20)
    time.sleep(2)
    run(["netsh", "wlan", "connect", f"name={ssid}"], timeout=30)
    wait_ping("8.8.8.8", timeout) or wait_ping("1.1.1.1", 15)


def json_get(blob: str, key: str) -> str:
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', blob)
    if m:
        return m.group(1).strip()
    m = re.search(rf'"{re.escape(key)}"\s*:\s*(-?\d+)', blob)
    return m.group(1).strip() if m else ""


def parse_uptime(text: str) -> str:
    up = re.search(r"\sup\s+(.+?)(?:,\s*load average|\s*$)", text or "", re.I | re.S)
    if not up:
        return "N/A"
    span = up.group(1).strip().rstrip(",")
    m = re.search(r"(\d+)\s+days?,\s+(\d+):(\d+)", span)
    if m:
        d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if d == 1:
            return f"1 day, {h:02d}:{mi:02d}"
        return f"{d} days, {h:02d}:{mi:02d}"
    m = re.search(r"(\d+):(\d+)", span)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    m = re.search(r"(\d+)\s+min", span)
    if m:
        return f"00:{int(m.group(1)):02d}"
    return "N/A"


def parse_ping(text: str) -> str:
    m = re.search(r"(\d+)%\s*packet loss", text or "", re.I)
    if not m:
        return "FAIL"
    return "PASS" if int(m.group(1)) == 0 else "FAIL"


def ssh_cmd(user: str, host: str, password: str, remote: str, timeout: int) -> str:
    ask = os.path.join(tempfile.gettempdir(), "simplifi-askpass.cmd")
    with open(ask, "w", encoding="ascii") as f:
        f.write(f"@echo off\r\necho {password}\r\n")
    env = os.environ.copy()
    env["SSH_ASKPASS"] = ask
    env["SSH_ASKPASS_REQUIRE"] = "force"
    env["DISPLAY"] = env.get("DISPLAY") or "localhost:0"
    try:
        r = subprocess.run(
            [
                "ssh",
                "-tt",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=NUL",
                "-o", "PreferredAuthentications=password,keyboard-interactive",
                "-o", "PubkeyAuthentication=no",
                "-o", f"ConnectTimeout={min(timeout, 25)}",
                f"{user}@{host}",
                remote,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return ""
    finally:
        try:
            os.remove(ask)
        except OSError:
            pass


def collect_one(job: dict, station: dict) -> dict:
    gw = job.get("gateway") or "192.168.2.1"
    alt = job.get("gateway_alt") or "192.168.10.1"
    ssh_user = job.get("ssh_user") or "root"
    timeout = int(job.get("wifi_timeout_sec") or 45)
    ssh_timeout = int(job.get("ssh_timeout_sec") or 30)
    ssid = station.get("ssid") or ""
    password = station.get("password") or ""
    row = {
        "name": station.get("name") or "",
        "imei": station.get("imei") or "",
        "anydesk": station.get("anydesk") or "",
        "firmware": "N/A",
        "uptime": "N/A",
        "carrier": "N/A",
        "phone": "N/A",
        "rssi": "N/A",
        "wifi_sim_status": "FAIL",
        "ssh_access": "FAIL",
        "note": "",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "voicelink_status": "N/A",
    }
    if not ssid:
        row["note"] = "ssid empty in monitor.conf"
        return row
    log("INFO", f"WiFi join {ssid} for {row['name']}")
    if not wifi_connect(ssid, password, timeout):
        row["note"] = f"WiFi join failed: {ssid}"
        log("FAILED", row["note"])
        return row
    host = gw if ping_ok(gw) else (alt if ping_ok(alt) else "")
    if not host:
        row["note"] = "router gateway not reachable"
        return row
    log("INFO", f"SSH {ssh_user}@{host}")
    ubus = ssh_cmd(ssh_user, host, password, "ubus call cellular status", ssh_timeout)
    if "root@Simplifi" in ubus or '"imei"' in ubus or '"operator"' in ubus:
        row["ssh_access"] = "PASS"
        imei = json_get(ubus, "imei")
        if imei and row["imei"] and imei != row["imei"]:
            row["note"] = f"IMEI mismatch got {imei}"
        row["carrier"] = json_get(ubus, "operator") or "N/A"
        row["phone"] = json_get(ubus, "msisdn") or "N/A"
        row["rssi"] = json_get(ubus, "rssi") or "N/A"
    fw = ssh_cmd(ssh_user, host, password, "simcom get firmware_version", ssh_timeout)
    for line in (fw or "").splitlines():
        t = line.strip()
        if t and not t.lower().startswith("simcom") and "password" not in t.lower():
            if re.match(r"^[\w.\-]+$", t) or re.match(r"^\d+\.\d+", t):
                row["firmware"] = t
                break
    up = ssh_cmd(ssh_user, host, password, "uptime", ssh_timeout)
    row["uptime"] = parse_uptime(up)
    ping = ssh_cmd(ssh_user, host, password, "ping -c 3 -W 3 google.com", ssh_timeout + 15)
    row["wifi_sim_status"] = parse_ping(ping)
    if row["ssh_access"] != "PASS":
        row["note"] = (row["note"] + " SSH failed").strip()
    return row


def main() -> int:
    global _log_fp
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    job_dir = os.path.dirname(os.path.abspath(args.job)) or "."
    log_path = os.path.join(job_dir, "collect.log")
    try:
        # Exclusive to this process — starter.cmd must not also redirect here.
        _log_fp = open(log_path, "a", encoding="utf-8")
    except PermissionError:
        alt = os.path.join(job_dir, f"collect_{os.getpid()}.log")
        try:
            _log_fp = open(alt, "a", encoding="utf-8")
            print(f"[WARN] collect.log locked — logging to {alt}", flush=True)
        except OSError:
            _log_fp = None
            print("[WARN] Cannot open collect.log — console only", flush=True)
    with open(args.job, encoding="utf-8") as f:
        job = json.load(f)
    out_path = args.out or job.get("out") or os.path.join(os.path.dirname(args.job), "results.json")
    restore = (job.get("restore_ssid") or "").strip() or current_ssid()
    log("INFO", f"Saved WiFi SSID={restore or '(none)'}")
    rows = []
    try:
        for st in job.get("stations") or []:
            rows.append(collect_one(job, st))
    finally:
        wifi_restore(restore, int(job.get("wifi_timeout_sec") or 45))
    payload = {"ok": True, "restore_ssid": restore, "rows": rows}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    log("INFO", f"Wrote {out_path} n={len(rows)}")
    if _log_fp:
        try:
            _log_fp.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("ERROR", f"{type(exc).__name__}: {exc}")
        sys.exit(1)
