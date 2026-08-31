"""Match Faxback queue rows: User + Received On within ±N minutes of monitor time."""
from __future__ import annotations

import re
from datetime import datetime

DEFAULT_USERS = ("simplifivn1", "simplifivn2")
DEFAULT_WINDOW_MIN = 7

# Tail of the Queues screenshot (newest rows). Older rows are not listed here.
SCREENSHOT_ROWS = (
    "Aug 31, 2026 04:05pm  84388164291  1  simplifivn2  PendingDeletion  1  0  0  Success\n"
    "Aug 31, 2026 04:05pm  84352738502  1  simplifivn1  PendingDeletion  1  0  0  Success\n"
)

_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

_TIME_RE = re.compile(
    r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?\b",
    re.IGNORECASE,
)
_FAIL_RE = re.compile(r"hung up|poor line|no answer|busy|failed", re.IGNORECASE)
_OK_RE = re.compile(r"success", re.IGNORECASE)
_JUNK_RE = re.compile(
    r"access denied|forgot password|try again|^login$",
    re.IGNORECASE,
)


def date_needles(when: datetime) -> list[str]:
    day = str(when.day)
    month_abbr = when.strftime("%b")
    month_full = when.strftime("%B")
    needles = [
        f"{month_abbr} {day}, {when.year}",
        f"{month_abbr} {when.day:02d}, {when.year}",
        f"{month_abbr} {day} {when.year}",
        f"{month_full} {day}, {when.year}",
        f"{month_full} {day} {when.year}",
        when.strftime("%Y-%m-%d"),
        when.strftime("%m/%d/%Y"),
        f"{when.month}/{when.day}/{when.year}",
        when.strftime("%d/%m/%Y"),
        f"{when.day:02d}/{when.month:02d}/{when.year}",
        f"{when.day:02d}-{month_abbr}-{when.year}",
        f"{day}-{month_abbr}-{when.year}",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for n in needles:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def date_hit(text: str, when: datetime) -> bool:
    t = text or ""
    tl = t.lower()
    for d in date_needles(when):
        if d.lower() in tl:
            return True
    if str(when.year) not in t:
        return False
    name = _MONTHS[when.month - 1]
    abbr = name[:3]
    mm = f"{when.month:02d}"
    dd = f"{when.day:02d}"
    has_month = (
        name in tl
        or abbr in tl
        or f"-{mm}-" in t
        or f"/{mm}/" in t
        or f"{mm}/" in t
        or f"{mm}-" in t
    )
    has_day = (
        f"-{dd}" in t
        or f"/{dd}" in t
        or f"{dd}/" in t
        or f"{dd}," in t
        or f" {dd}," in t
        or f" {when.day}," in t
        or f" {when.day} " in t
    )
    return has_month and has_day


def parse_clock_minutes(text: str) -> list[int]:
    """Minutes from midnight for each clock in text (04:05pm → 16*60+5)."""
    out: list[int] = []
    for m in _TIME_RE.finditer(text or ""):
        hour = int(m.group(1))
        minute = int(m.group(2))
        ap = (m.group(4) or "").lower()
        if minute > 59:
            continue
        if ap == "pm" and hour < 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        if not ap and hour > 23:
            continue
        out.append(hour * 60 + minute)
    return out


def minutes_apart(a: int, b: int) -> int:
    d = abs(a - b)
    return min(d, 1440 - d)


def time_hit(text: str, when: datetime, window_min: int = DEFAULT_WINDOW_MIN) -> bool:
    if not date_hit(text, when):
        return False
    times = parse_clock_minutes(text)
    if not times:
        return False
    target = when.hour * 60 + when.minute
    return any(minutes_apart(t, target) <= window_min for t in times)


def user_in(text: str, user: str) -> bool:
    return bool(user) and user.lower() in (text or "").lower()


def row_is_pass(
    text: str,
    user: str,
    when: datetime,
    window_min: int = DEFAULT_WINDOW_MIN,
) -> bool:
    t = re.sub(r"\s+", " ", text or "").strip()
    if not user_in(t, user) or not time_hit(t, when, window_min):
        return False
    if _FAIL_RE.search(t) and not _OK_RE.search(t):
        return False
    return True


def is_junk_row(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _JUNK_RE.search(t) and "simplifivn" not in t.lower():
        return True
    return False


def matched_our_user(
    text: str,
    users: list[str] | tuple[str, ...],
    when: datetime,
    window_min: int = DEFAULT_WINDOW_MIN,
) -> str | None:
    for u in users:
        if row_is_pass(text, u, when, window_min):
            return u
    return None


def split_rows(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip() and not is_junk_row(ln)]


def q1_index(n: int) -> int:
    """0-based first-quartile row from the top. Search last row down to here."""
    if n <= 1:
        return 0
    return int((n - 1) * 0.25)


def match_users(
    text: str,
    users: list[str] | tuple[str, ...],
    when: datetime,
    window_min: int = DEFAULT_WINDOW_MIN,
) -> dict[str, bool]:
    found, _meta = match_rows_from_end(split_rows(text), users, when, window_min)
    return found


def match_last_pair(
    rows: list[str],
    users: list[str] | tuple[str, ...],
    when: datetime,
    window_min: int = DEFAULT_WINDOW_MIN,
) -> tuple[dict[str, bool], dict[str, str | int]]:
    """Last row only; if it is vn1/vn2 + time match, check the row above for the other."""
    found = {u: False for u in users}
    clean = [r for r in rows if not is_junk_row(r)]
    meta: dict[str, str | int] = {
        "n": len(clean),
        "last": "",
        "prev": "",
        "lastUser": "",
        "prevUser": "",
    }
    if not clean:
        return found, meta
    last = clean[-1]
    meta["last"] = last[:140]
    u_last = matched_our_user(last, users, when, window_min)
    if not u_last:
        return found, meta
    found[u_last] = True
    meta["lastUser"] = u_last
    if len(clean) < 2:
        return found, meta
    prev = clean[-2]
    meta["prev"] = prev[:140]
    u_prev = matched_our_user(prev, users, when, window_min)
    if u_prev:
        meta["prevUser"] = u_prev
        if u_prev != u_last:
            found[u_prev] = True
    return found, meta


def match_rows_from_end(
    rows: list[str],
    users: list[str] | tuple[str, ...],
    when: datetime,
    window_min: int = DEFAULT_WINDOW_MIN,
) -> tuple[dict[str, bool], dict[str, int]]:
    """Newest row first, walk up, stop at Q1 (skip the oldest quarter)."""
    found = {u: False for u in users}
    clean = [r for r in rows if not is_junk_row(r)]
    n = len(clean)
    q1 = q1_index(n)
    scanned = 0
    for i in range(n - 1, q1 - 1, -1):
        scanned += 1
        for u in users:
            if not found[u] and row_is_pass(clean[i], u, when, window_min):
                found[u] = True
        if found and all(found[u] for u in users):
            break
    return found, {"n": n, "q1": q1, "scanned": scanned}
