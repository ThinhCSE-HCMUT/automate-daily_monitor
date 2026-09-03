#!/usr/bin/env python3
"""
Pi side: after VN Voicelink/Fax SSH PASS, drive the US Windows jump laptop.

  1. scp us_jump_collect.py + job.json to the laptop (Tailscale)
  2. start the collector detached (laptop may drop Tailscale while hopping WiFi)
  3. wait until the laptop is back on Tailscale
  4. fetch results.json and upsert today's CSV rows

  python3 scripts/us_jump_run.py --conf monitor.conf --csv output/daily_monitor.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime


CSV_FIELDS = [
    "Date",
    "Anydesk ID",
    "IMEI",
    "Firmware Version",
    "Uptime (hh:mm)",
    "Voicelink/Fax status",
    "Carrier",
    "Phone",
    "RSSI (dBm)",
    "WiFi Status (Sim Data)",
    "SSH Access",
    "Note",
]


def log(level: str, msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{level}] {msg}", flush=True)


def load_conf(path: str) -> dict[str, str]:
    cfg: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            cfg[key.strip()] = val.strip()
    return cfg


def routers_from_conf(cfg: dict[str, str]) -> list[dict[str, str]]:
    idxs = set()
    for key in cfg:
        if key.startswith("router.") and key.count(".") >= 2:
            idxs.add(int(key.split(".")[1]))
    out = []
    for i in sorted(idxs):
        access = (cfg.get(f"router.{i}.access") or "wifi").lower()
        if access != "tailscale":
            continue
        out.append(
            {
                "name": cfg.get(f"router.{i}.name") or "",
                "imei": cfg.get(f"router.{i}.imei") or "",
                "anydesk": cfg.get(f"router.{i}.anydesk") or "",
                "ssid": cfg.get(f"router.{i}.ssid") or "",
                "password": cfg.get(f"router.{i}.password") or "",
            }
        )
    return out


def ssh_base(user: str, host: str) -> list[str]:
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=20",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{user}@{host}",
    ]


def scp_base(user: str, host: str) -> list[str]:
    return [
        "scp",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=20",
        "-o", "StrictHostKeyChecking=accept-new",
    ]


def run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ping_host(host: str) -> bool:
    r = run(["ping", "-c", "1", "-W", "2", host], timeout=8)
    return r.returncode == 0


def csv_day(stamp: str) -> str:
    s = (stamp or "").strip().strip('"')
    return s[:10]


def upsert_csv(path: str, rows: list[dict]) -> None:
    existing: list[dict[str, str]] = []
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for rec in reader:
                existing.append({k.strip(): (v or "").strip() for k, v in rec.items() if k})
    by_key: dict[tuple[str, str], dict[str, str]] = {}
    order: list[tuple[str, str]] = []
    for rec in existing:
        key = (csv_day(rec.get("Date") or ""), rec.get("IMEI") or "")
        if key not in by_key:
            order.append(key)
        by_key[key] = rec
    for row in rows:
        rec = {
            "Date": row.get("date") or "",
            "Anydesk ID": row.get("anydesk") or "",
            "IMEI": row.get("imei") or "",
            "Firmware Version": row.get("firmware") or "N/A",
            "Uptime (hh:mm)": row.get("uptime") or "N/A",
            "Voicelink/Fax status": row.get("voicelink_status") or "N/A",
            "Carrier": row.get("carrier") or "N/A",
            "Phone": row.get("phone") or "N/A",
            "RSSI (dBm)": row.get("rssi") or "N/A",
            "WiFi Status (Sim Data)": row.get("wifi_sim_status") or "FAIL",
            "SSH Access": row.get("ssh_access") or "FAIL",
            "Note": row.get("note") or "",
        }
        key = (csv_day(rec["Date"]), rec["IMEI"])
        if key not in by_key:
            order.append(key)
        by_key[key] = rec
        log("INFO", f"CSV upsert {rec['IMEI']} SSH={rec['SSH Access']} up={rec['Uptime (hh:mm)']}")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for key in order:
            rec = by_key[key]
            w.writerow({k: rec.get(k, "") for k in CSV_FIELDS})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", default="monitor.conf")
    parser.add_argument("--csv", default="output/daily_monitor.csv")
    args = parser.parse_args()

    cfg = load_conf(args.conf)
    host = cfg.get("jump_host") or ""
    user = cfg.get("jump_user") or "simplifi"
    wait_sec = int(cfg.get("jump_wait_sec") or "600")
    stations = routers_from_conf(cfg)
    if not host:
        log("WARN", "jump_host empty — skip US Virtual Stations")
        return 0
    if not stations:
        log("WARN", "No router.N.access=tailscale stations — skip US")
        return 0
    missing_ssid = [s["name"] for s in stations if not s.get("ssid")]
    if missing_ssid:
        log("WARN", f"Fill router.N.ssid for: {', '.join(missing_ssid)}")

    here = os.path.dirname(os.path.abspath(__file__))
    collect_py = os.path.join(here, "us_jump_collect.py")
    if not os.path.isfile(collect_py):
        log("ERROR", f"Missing {collect_py}")
        return 1

    log("INFO", f"Jump {user}@{host}  stations={len(stations)}")
    probe = run(ssh_base(user, host) + ["echo JUMP_OK"], timeout=25)
    if probe.returncode != 0 or "JUMP_OK" not in (probe.stdout or ""):
        log("ERROR", f"Cannot SSH jump host (need key login): {(probe.stderr or '')[:300]}")
        log("ERROR", f"On the Pi: ssh-copy-id {user}@{host}")
        return 1
    py_chk = run(ssh_base(user, host) + ["py", "-3", "-c", "print(88)"], timeout=25)
    if py_chk.returncode != 0 or "88" not in (py_chk.stdout or ""):
        log("ERROR", "US laptop needs Python 3 launcher (py -3). Install python.org Windows installer, tick 'py launcher'.")
        return 1

    remote_dir = f"C:/Users/{user}/simplifi-monitor"
    win_dir = remote_dir.replace("/", "\\")
    run(ssh_base(user, host) + ["cmd", "/c", f"if not exist {win_dir} mkdir {win_dir}"], timeout=20)
    job = {
        "gateway": cfg.get("router_gateway") or "192.168.2.1",
        "gateway_alt": cfg.get("router_gateway_alt") or "192.168.10.1",
        "ssh_user": cfg.get("ssh_user") or "root",
        "wifi_timeout_sec": int(cfg.get("wifi_timeout_sec") or "45"),
        "ssh_timeout_sec": int(cfg.get("ssh_timeout_sec") or "45"),
        "restore_ssid": cfg.get("jump_restore_ssid") or "",
        "out": f"{remote_dir}/results.json",
        "stations": stations,
    }
    job_local = os.path.join("output", "us_jump_job.json")
    os.makedirs("output", exist_ok=True)
    with open(job_local, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=2)

    for src, dst in (
        (collect_py, f"{user}@{host}:{remote_dir}/us_jump_collect.py"),
        (job_local, f"{user}@{host}:{remote_dir}/job.json"),
    ):
        r = run(scp_base(user, host) + [src, dst], timeout=30)
        if r.returncode != 0:
            log("ERROR", f"scp failed: {(r.stderr or '')[:300]}")
            return 1
    log("INFO", "Copied collector to US laptop")
    win_dir = remote_dir.replace("/", "\\")
    start = run(
        ssh_base(user, host)
        + [
            "cmd",
            "/c",
            "start",
            "/min",
            "py",
            "-3",
            f"{win_dir}\\us_jump_collect.py",
            "--job",
            f"{win_dir}\\job.json",
        ],
        timeout=30,
    )
    log("INFO", f"Started US WiFi-hop collector (ssh rc={start.returncode})")
    log("INFO", f"Waiting up to {wait_sec}s for laptop Tailscale {host} to return")

    time.sleep(8)
    deadline = time.time() + wait_sec
    back = False
    while time.time() < deadline:
        if ping_host(host):
            probe = run(ssh_base(user, host) + ["echo BACK"], timeout=20)
            if probe.returncode == 0 and "BACK" in (probe.stdout or ""):
                # results.json exists?
                chk = run(
                    ssh_base(user, host)
                    + ["cmd", "/c", f"if exist {win_dir}\\results.json echo HAS"],
                    timeout=20,
                )
                text = (chk.stdout or "") + (chk.stderr or "")
                if "HAS" in text:
                    back = True
                    break
                log("INFO", "Laptop is back; collector still writing results ...")
        time.sleep(8)

    if not back:
        log("ERROR", "US laptop did not return results.json in time (Tailscale/WiFi restore?)")
        return 1

    local_results = os.path.join("output", "us_jump_results.json")
    r = run(
        scp_base(user, host) + [f"{user}@{host}:{remote_dir}/results.json", local_results],
        timeout=30,
    )
    if r.returncode != 0:
        log("ERROR", f"scp results failed: {(r.stderr or '')[:300]}")
        return 1
    with open(local_results, encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload.get("rows") or []
    if not rows:
        log("WARN", "US collector returned no rows")
        return 1
    upsert_csv(args.csv, rows)
    log("PASSED", f"US Virtual Stations: {len(rows)} row(s) in {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
