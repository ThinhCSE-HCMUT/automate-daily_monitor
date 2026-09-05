#!/usr/bin/env python3
"""
Analyze today's 6 STG portal router logs with a Cursor agent (cursor-sdk) and
write the verdict into the Note column of daily_monitor.csv, which
sharepoint_excel.py then pushes to SharePoint.

  .venv/bin/python3 scripts/analyze_router_logs.py --conf monitor.conf --csv output/daily_monitor.csv

Needs on the Pi (64-bit OS):
  .venv/bin/pip install cursor-sdk
  cursor_api_key=cursor_...   in monitor.conf   (or env CURSOR_API_KEY)

Failures never break the monitor flow: the script logs a WARN and exits 0,
leaving Note untouched.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime

# Lines worth showing to the AI. Everything else is noise for this purpose.
ISSUE_PATTERNS = re.compile(
    r"error|fail|fatal|crash|panic|reboot|restart|watchdog|segfault|denied|"
    r"timeout|unreachable|disconnect|dropped|no carrier|sim (?:not|missing|error)|"
    r"reset|refused|critical|emerg|alert",
    re.I,
)
TAIL_LINES = 30
MAX_CHARS_PER_LOG = 6000
MAX_KEYWORDS = 3

PROMPT_HEADER = """You are reviewing daily logs from Simplifi cellular routers.
For EACH router below, decide if it operated normally or had an issue.
Only report real problems (repeated disconnects, reboots, SIM/cellular errors,
crashes, auth failures). Single transient warnings that recovered are Normal.

Reply with ONLY a JSON array, no markdown, one object per router:
[{"imei": "<imei>", "status": "Normal" or "ISSUE",
  "keywords": ["SHORT_KEYWORD", ...], "summary": "<one short sentence>"}]

keywords: at most 3, UPPER_SNAKE_CASE (e.g. CELLULAR_DISCONNECT, REBOOT,
SIM_ERROR, AUTH_FAILURE, LOW_SIGNAL). Empty list when status is Normal.
"""


def log(level: str, msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    flow = (os.environ.get("MONITOR_FLOW") or "").strip()
    tag = f" [{flow}]" if flow else ""
    print(f"[{now}] [{level}]{tag} {msg}", flush=True)


def load_conf(path: str) -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not os.path.isfile(path):
        return cfg
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            cfg[key.strip()] = val.strip()
    return cfg


def today_log_dir(out_root: str, now: datetime) -> str:
    return os.path.join(os.path.abspath(out_root), "routers_log", now.strftime("%d_%m_%Y"))


def excerpt_log(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    hits = [ln for ln in lines if ISSUE_PATTERNS.search(ln)]
    tail = lines[-TAIL_LINES:]
    seen: set[str] = set()
    picked: list[str] = []
    for ln in hits + ["--- last lines ---"] + tail:
        if ln in seen:
            continue
        seen.add(ln)
        picked.append(ln)
    text = "\n".join(picked)
    if len(text) > MAX_CHARS_PER_LOG:
        text = text[:MAX_CHARS_PER_LOG] + "\n...[truncated]"
    return text


def collect_excerpts(day_dir: str) -> dict[str, str]:
    """IMEI -> filtered log text, from files named DDMMYYYY_IMEI.log."""
    excerpts: dict[str, str] = {}
    for name in sorted(os.listdir(day_dir)):
        m = re.match(r"^\d{8}_(\d{14,16})\.log$", name)
        if not m:
            continue
        excerpts[m.group(1)] = excerpt_log(os.path.join(day_dir, name))
    return excerpts


def build_prompt(excerpts: dict[str, str]) -> str:
    parts = [PROMPT_HEADER]
    for imei, text in excerpts.items():
        parts.append(f"===== Router IMEI {imei} =====\n{text or '(log empty)'}\n")
    return "\n".join(parts)


def ask_cursor(prompt: str, api_key: str, cwd: str, model: str) -> str:
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    result = Agent.prompt(
        prompt,
        AgentOptions(
            api_key=api_key,
            model=model,
            local=LocalAgentOptions(cwd=cwd),
        ),
    )
    if result.status != "finished":
        raise RuntimeError(f"agent run status={result.status}")
    return result.result or ""


def parse_verdicts(reply: str) -> dict[str, dict]:
    m = re.search(r"\[.*\]", reply, re.S)
    if not m:
        raise ValueError(f"no JSON array in reply: {reply[:200]!r}")
    out: dict[str, dict] = {}
    for item in json.loads(m.group(0)):
        imei = str(item.get("imei") or "").strip()
        if imei:
            out[imei] = item
    return out


def note_text(verdict: dict) -> str:
    status = str(verdict.get("status") or "").strip().upper()
    if status == "NORMAL":
        return "Normal"
    kws = [str(k).strip() for k in (verdict.get("keywords") or []) if str(k).strip()]
    if kws:
        return "ISSUE: " + ", ".join(kws[:MAX_KEYWORDS])
    summary = str(verdict.get("summary") or "").strip()
    return f"ISSUE: {summary[:80]}" if summary else "ISSUE"


def update_csv_notes(csv_path: str, verdicts: dict[str, dict], today: str) -> int:
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    if "Note" not in fields:
        log("WARN", f"CSV has no Note column: {csv_path}")
        return 0
    changed = 0
    for rec in rows:
        day = (rec.get("Date") or "").strip()[:10]
        imei = (rec.get("IMEI") or "").strip()
        verdict = verdicts.get(imei)
        if day != today or not verdict:
            continue
        current = (rec.get("Note") or "").strip()
        new_note = note_text(verdict)
        if current and current != new_note:
            log("INFO", f"{imei}: Note already {current!r} — keep it, analysis was {new_note!r}")
            continue
        rec["Note"] = new_note
        changed += 1
        log("PASSED", f"{imei}: Note = {new_note}")
    if changed:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", default="monitor.conf")
    parser.add_argument("--csv", default="output/daily_monitor.csv")
    parser.add_argument("--out", default="output", help="root that holds routers_log/")
    parser.add_argument("--model", default="composer-2.5")
    args = parser.parse_args()

    cfg = load_conf(args.conf)
    api_key = cfg.get("cursor_api_key") or os.environ.get("CURSOR_API_KEY") or ""
    if not api_key:
        log("WARN", f"No cursor_api_key in {args.conf} and no CURSOR_API_KEY env — skip log analysis")
        return 0

    now = datetime.now()
    day_dir = today_log_dir(args.out, now)
    if not os.path.isdir(day_dir):
        log("WARN", f"No portal log folder for today: {day_dir} — skip log analysis")
        return 0
    excerpts = collect_excerpts(day_dir)
    if not excerpts:
        log("WARN", f"No IMEI .log files in {day_dir} — skip log analysis")
        return 0
    if not os.path.isfile(args.csv):
        log("WARN", f"CSV not found: {args.csv} — skip log analysis")
        return 0

    log("INFO", f"Analyzing {len(excerpts)} router log(s) from {day_dir} via Cursor agent")
    try:
        reply = ask_cursor(build_prompt(excerpts), api_key, os.getcwd(), args.model)
        verdicts = parse_verdicts(reply)
    except ImportError:
        log("WARN", "cursor-sdk not installed (.venv/bin/pip install cursor-sdk) — skip log analysis")
        return 0
    except Exception as exc:
        log("WARN", f"Cursor analysis failed: {type(exc).__name__}: {exc} — Note left unchanged")
        return 0

    report = os.path.join(args.out, f"log_analysis_{now.strftime('%Y%m%d')}.json")
    with open(report, "w", encoding="utf-8") as f:
        json.dump(verdicts, f, indent=2, ensure_ascii=False)
    log("INFO", f"Analysis saved: {report}")

    missing = [i for i in excerpts if i not in verdicts]
    if missing:
        log("WARN", f"AI reply had no verdict for IMEI(s): {', '.join(missing)}")

    changed = update_csv_notes(args.csv, verdicts, now.strftime("%Y-%m-%d"))
    log("PASSED" if changed else "INFO", f"Updated Note for {changed} CSV row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
