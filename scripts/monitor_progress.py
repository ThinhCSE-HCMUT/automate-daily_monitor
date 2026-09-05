#!/usr/bin/env python3
"""Helpers for config_web Monitor Progress tab (status / start / stop)."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from typing import Any

FLOW_FRIENDLY = {
    "SETUP": "Starting the daily monitor…",
    "LAB WIFI": "Connecting the Pi to lab Wi‑Fi…",
    "VN SSH": "Checking Vietnam stations (Wi‑Fi + SSH)…",
    "US SSH": "Collecting US Virtual Stations via the Tailscale jump laptop…",
    "PORTAL GET LOG": "Downloading developer logs from the Simplifi portal…",
    "LOG ANALYSIS": "Analyzing router logs…",
    "FAX PRINT": "Sending test faxes and checking the fax queue…",
    "LAPTOP SYNC": "Copying results to the lab laptop…",
    "FILL SHEET": "Updating the SharePoint monitoring spreadsheet…",
    "DONE": "Daily monitor finished.",
    "IDLE": "Monitor is idle. Press Start to run the daily job.",
}

FLOW_PCT_DEFAULT = {
    "SETUP": 2,
    "LAB WIFI": 5,
    "VN SSH": 25,
    "US SSH": 50,
    "PORTAL GET LOG": 65,
    "LOG ANALYSIS": 72,
    "FAX PRINT": 80,
    "LAPTOP SYNC": 88,
    "FILL SHEET": 95,
    "DONE": 100,
    "IDLE": 0,
}


def project_root(monitor_conf: str) -> str:
    path = os.path.abspath(monitor_conf)
    parent = os.path.dirname(path)
    return parent if parent else os.getcwd()


def pid_path(root: str) -> str:
    return os.path.join(root, "output", "monitor.pid")


def status_path(root: str) -> str:
    return os.path.join(root, "output", "monitor_status.json")


def resolve_log_file(root: str, monitor_conf: str) -> str:
    log = "output/monitor.log"
    conf = os.path.join(root, monitor_conf) if not os.path.isabs(monitor_conf) else monitor_conf
    if os.path.isfile(conf):
        with open(conf, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("log_file="):
                    log = line.split("=", 1)[1].strip() or log
                    break
    if not os.path.isabs(log):
        log = os.path.join(root, log)
    return log


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid(root: str) -> int | None:
    path = pid_path(root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            pid = int((f.read() or "").strip())
        return pid if pid_alive(pid) else None
    except (OSError, ValueError):
        return None


def find_monitor_pids(root: str) -> list[int]:
    """Best-effort: pgrep for the project monitor binary."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "automate-daily_monitor/monitor"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        try:
            out = subprocess.check_output(
                ["pgrep", "-x", "monitor"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return [int(x) for x in out.split() if x.strip().isdigit()]
        except Exception:
            return []


def is_running(root: str) -> bool:
    if read_pid(root):
        return True
    return bool(find_monitor_pids(root))


def read_status_json(root: str) -> dict[str, Any]:
    path = status_path(root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def tail_log_line(log_file: str) -> str:
    if not os.path.isfile(log_file):
        return ""
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in reversed(lines[-80:]):
            s = line.strip()
            if s:
                return s
    except OSError:
        return ""
    return ""


def parse_flow_from_log(line: str) -> str:
    # [ts] [LEVEL] [FLOW] message
    parts = line.split("] ", 3)
    if len(parts) >= 3 and parts[2].startswith("["):
        flow = parts[2].strip()
        if flow.startswith("[") and flow.endswith("]"):
            return flow[1:-1]
    return ""


def paraphrase(flow: str, raw: str) -> str:
    if flow in FLOW_FRIENDLY:
        base = FLOW_FRIENDLY[flow]
    else:
        base = "Daily monitor is working…"
    # Add a short hint from the raw line when useful
    low = (raw or "").lower()
    if "permission denied" in low:
        return base + " SSH login was denied — check the station password."
    if "failed to connect to wifi" in low:
        return base + " Could not join a station Wi‑Fi."
    if "resourceLocked" in raw or "423" in raw:
        return base + " SharePoint file is locked by another editor."
    if "jump_host empty" in low:
        return base + " Jump host is not configured."
    if "finished" in low:
        return FLOW_FRIENDLY["DONE"]
    return base


def progress_snapshot(monitor_conf: str) -> dict[str, Any]:
    root = project_root(monitor_conf)
    running = is_running(root)
    st = read_status_json(root)
    log_file = resolve_log_file(root, monitor_conf)
    raw = tail_log_line(log_file)
    flow = (st.get("flow") or parse_flow_from_log(raw) or "").strip()
    try:
        percent = int(st.get("percent") if st.get("percent") is not None else -1)
    except (TypeError, ValueError):
        percent = -1
    if running:
        if not flow:
            flow = "SETUP"
        if percent < 0:
            percent = FLOW_PCT_DEFAULT.get(flow, 5)
    else:
        if flow == "DONE" or (raw and "finished" in raw.lower()):
            flow = "DONE"
            percent = 100
        else:
            flow = "IDLE"
            percent = 0
    friendly = paraphrase(flow, raw)
    return {
        "running": running,
        "flow": flow,
        "percent": max(0, min(100, percent)),
        "message": friendly,
        "raw": raw,
        "updated": st.get("updated") or "",
    }


def start_monitor(monitor_conf: str) -> tuple[bool, str]:
    root = project_root(monitor_conf)
    if is_running(root):
        return False, "Daily monitor is already running."
    mon_bin = os.path.join(root, "monitor")
    if not os.path.isfile(mon_bin) or not os.access(mon_bin, os.X_OK):
        return False, f"Monitor binary not found or not executable: {mon_bin} (run make on the Pi)."
    conf = monitor_conf if os.path.isabs(monitor_conf) else os.path.join(root, monitor_conf)
    os.makedirs(os.path.join(root, "output"), exist_ok=True)
    # Reset status for UI
    with open(status_path(root), "w", encoding="utf-8") as f:
        json.dump(
            {
                "running": True,
                "flow": "SETUP",
                "percent": 1,
                "message": "Starting…",
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            f,
        )
    try:
        proc = subprocess.Popen(
            [mon_bin, "-c", conf],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return False, f"Could not start monitor: {exc}"
    with open(pid_path(root), "w", encoding="utf-8") as f:
        f.write(str(proc.pid))
    return True, f"Started daily monitor (pid {proc.pid})."


def stop_monitor(monitor_conf: str) -> tuple[bool, str]:
    root = project_root(monitor_conf)
    pids = []
    pid = read_pid(root)
    if pid:
        pids.append(pid)
    for p in find_monitor_pids(root):
        if p not in pids:
            pids.append(p)
    if not pids:
        # clear stale status
        with open(status_path(root), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "running": False,
                    "flow": "IDLE",
                    "percent": 0,
                    "message": "Idle",
                    "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                f,
            )
        try:
            os.remove(pid_path(root))
        except OSError:
            pass
        return False, "Monitor is not running."
    killed = []
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
            killed.append(p)
        except OSError:
            pass
    time.sleep(1)
    for p in list(killed):
        if pid_alive(p):
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
    try:
        os.remove(pid_path(root))
    except OSError:
        pass
    with open(status_path(root), "w", encoding="utf-8") as f:
        json.dump(
            {
                "running": False,
                "flow": "IDLE",
                "percent": 0,
                "message": "Stopped by user",
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            f,
        )
    return True, f"Stopped monitor (pid {', '.join(str(x) for x in killed)})."
