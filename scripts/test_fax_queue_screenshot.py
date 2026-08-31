#!/usr/bin/env python3
"""
Laptop test of the Faxback queue matcher — does NOT send a fax.

Same rules as scripts/send_fax.py:
  start at the last row, walk upward, stop at Q1 (first quartile from the top)
  User simplifivn1 + simplifivn2
  Received On = monitor calendar day
  clock within +/-7 minutes of monitor start
  Status Success

  python scripts/test_fax_queue_screenshot.py
  python scripts/test_fax_queue_screenshot.py --at "2026-08-31 16:04"
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from fax_queue_match import (  # noqa: E402
    DEFAULT_USERS,
    DEFAULT_WINDOW_MIN,
    SCREENSHOT_ROWS,
    match_last_pair,
    match_rows_from_end,
    parse_clock_minutes,
    q1_index,
    row_is_pass,
    split_rows,
)


def log(level: str, msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{level}] {msg}", flush=True)


def parse_at(raw: str) -> datetime:
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            got = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                return got.replace(hour=16, minute=4)
            return got
        except ValueError:
            continue
    raise SystemExit(f"Cannot parse --at {raw!r} (use YYYY-MM-DD HH:MM)")


def status_line(found: dict[str, bool]) -> str:
    return " ".join(f"{u}={'PASS' if found.get(u) else 'FAIL'}" for u in DEFAULT_USERS)


def fake_row(user: str, clock: str = "04:05pm") -> str:
    phone = "84388164291" if "vn2" in user else "84352738502"
    return (
        f"Aug 31, 2026 {clock}  {phone}  1  {user}  "
        f"PendingDeletion  1  0  0  Success"
    )


def crowded_table(tail_ours: bool) -> str:
    """12 rows: Q1 skips the top 2. Decoys sit above Q1; other printers fill the middle."""
    decoy = [fake_row("simplifivn1"), fake_row("simplifivn2")]
    others = [fake_row(f"otheruser{i}") for i in range(8)]
    if tail_ours:
        tail = split_rows(SCREENSHOT_ROWS)
    else:
        tail = [fake_row("otherA"), fake_row("otherB")]
    return "\n".join(decoy + others + tail)


def run_pair(label: str, text: str, when: datetime, window_min: int, expect_pass: bool) -> bool:
    rows = split_rows(text)
    found, meta = match_last_pair(rows, list(DEFAULT_USERS), when, window_min)
    ok = all(found.values()) if expect_pass else not all(found.values())
    level = "PASSED" if ok else "FAILED"
    log(
        level,
        f"{label}: {status_line(found)}  last={meta.get('lastUser') or '-'} "
        f"prev={meta.get('prevUser') or '-'} monitor={when.strftime('%H:%M')}",
    )
    return ok


def run_q1(label: str, text: str, when: datetime, window_min: int, expect_pass: bool) -> bool:
    rows = split_rows(text)
    found, meta = match_rows_from_end(rows, list(DEFAULT_USERS), when, window_min)
    ok = all(found.values()) if expect_pass else not any(found.values())
    level = "PASSED" if ok else "FAILED"
    log(
        level,
        f"{label}: {status_line(found)}  n={meta['n']} q1={meta['q1']} "
        f"scanned={meta['scanned']} monitor={when.strftime('%H:%M')}",
    )
    n = len(rows)
    q1 = q1_index(n)
    for i in range(n - 1, q1 - 1, -1):
        line = rows[i]
        clocks = parse_clock_minutes(line)
        clock_s = ",".join(f"{c // 60:02d}:{c % 60:02d}" for c in clocks) or "-"
        hits = [u for u in DEFAULT_USERS if row_is_pass(line, u, when, window_min)]
        log("DEBUG", f"  [{i}] clock={clock_s} users={hits or '-'} | {line[:120]}")
        if all(found.values()):
            break
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--at",
        default="2026-08-31 16:04",
        help="Monitor start time (screenshot fax is 16:05 / 04:05pm)",
    )
    parser.add_argument("--window-min", type=int, default=DEFAULT_WINDOW_MIN)
    parser.add_argument(
        "--image",
        default=os.path.join(HERE, "fixtures", "fax_queue_tail.png"),
        help="Screenshot path (logged only; rows are transcribed)",
    )
    args = parser.parse_args()
    when = parse_at(args.at)
    window_min = args.window_min

    log("INFO", "No fax will be sent - matcher only")
    log("INFO", "Phase 1: last row + row above. Phase 2 (after 7 min): last -> Q1")
    log("INFO", f"Users {', '.join(DEFAULT_USERS)}; same day; clock +/-{window_min} min")
    if args.image and os.path.isfile(args.image):
        log("INFO", f"Screenshot: {os.path.abspath(args.image)}")

    ok = True
    ok &= run_pair("pair: screenshot last two", SCREENSHOT_ROWS, when, window_min, True)
    others_then_ours = "\n".join(
        [fake_row("otheruser")] + split_rows(SCREENSHOT_ROWS)
    )
    ok &= run_pair("pair: other then our two at tail", others_then_ours, when, window_min, True)
    ok &= run_pair(
        "pair: last is other (do nothing)",
        crowded_table(False),
        when,
        window_min,
        False,
    )

    too_late = when + timedelta(minutes=window_min + 2)
    ok &= run_pair("pair: outside +/-7 min", SCREENSHOT_ROWS, too_late, window_min, False)

    ok &= run_q1("q1: ours at bottom, decoys above Q1", crowded_table(True), when, window_min, True)
    ok &= run_q1("q1: only decoys above Q1", crowded_table(False), when, window_min, False)

    yesterday = when - timedelta(days=1)
    ok &= run_pair("pair: yesterday", SCREENSHOT_ROWS, yesterday, window_min, False)

    if ok:
        log("PASSED", "Last-pair + Q1 matcher agrees with the screenshot")
        return 0
    log("FAILED", "Matcher did not match as expected")
    return 1


if __name__ == "__main__":
    sys.exit(main())
