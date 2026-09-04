#!/usr/bin/env python3
"""
On-demand local web UI to edit daily_monitor configs.

  .venv/bin/python3 scripts/config_web.py
  # open http://<pi-ip>:8765
  # Ctrl+C when done — do NOT run as a 24/7 service
"""
from __future__ import annotations

import argparse
import html
import io
import json
import os
import re
import sys
from email import message_from_bytes
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from stations_lib import (
    PORTAL_KEYS,
    SHAREPOINT_KEYS,
    default_status_header,
    fax_users_from_stations,
    load_conf,
    load_stations,
    replace_router_block,
    write_conf_updates,
    write_portal_imeis_csv,
)

STATE: dict = {}

MONITOR_KEYS = (
    "lab_ssid_5g",
    "lab_ssid_24g",
    "lab_password",
    "jump_host",
    "jump_user",
    "cursor_api_key",
)

FAX_BASIC_KEYS = ("username", "password", "url")

DEFAULT_FAX_ATTACH = "scripts/fax_message.txt"
FAX_UPLOAD_DIR = "scripts/fax_uploads"
MAX_FAX_ATTACH_BYTES = 100 * 1024 * 1024
ALLOWED_FAX_EXT = {".txt", ".pdf"}

# Inline SVG icons (currentColor) — no external assets.
_ICON = lambda d: (
    f'<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">'
    f'<path fill="currentColor" d="{d}"/></svg>'
)
TAB_ICONS = {
    # router / stations
    "stations": _ICON(
        "M12 2a3 3 0 0 1 3 3v1h2a2 2 0 0 1 2 2v2h1a2 2 0 0 1 0 4h-1v2a2 2 0 0 1-2 2h-2v1a3 3 0 1 1-6 0v-1H7a2 2 0 0 1-2-2v-2H4a2 2 0 0 1 0-4h1V8a2 2 0 0 1 2-2h2V5a3 3 0 0 1 3-3zm0 2a1 1 0 0 0-1 1v2H7v4H5v0h2v4h4v2h2v-2h4v-4h2v0h-2V7h-4V5a1 1 0 0 0-1-1z"
    ),
    # portal / globe-login
    "portal": _ICON(
        "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 2c1.7 0 3.3.5 4.6 1.4L12 10.9 7.4 5.4A8 8 0 0 1 12 4zm-6.1 2.5L10.9 12 5.9 17A8 8 0 0 1 5.9 6.5zM12 20a8 8 0 0 1-4.6-1.4L12 13.1l4.6 5.5A8 8 0 0 1 12 20zm6.1-3L13.1 12l5-5.5a8 8 0 0 1 0 11z"
    ),
    # fax / document send
    "fax": _ICON(
        "M6 2h8l4 4v4h-2V7h-3V4H7v5H5V4a2 2 0 0 1 2-2zm-1 9h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zm2 3v5h10v-5H7z"
    ),
    # sharepoint-like grid / cloud file
    "sharepoint": _ICON(
        "M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-6zm0 2.5L17.5 8H14V4.5zM8 12h8v2H8v-2zm0 4h8v2H8v-2zM8 8h3v2H8V8z"
    ),
    # lab wifi / jump
    "lab": _ICON(
        "M12 18a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm0-5a6.5 6.5 0 0 1 4.6 1.9l-1.4 1.4A4.5 4.5 0 0 0 12 15a4.5 4.5 0 0 0-3.2 1.3L7.4 14.9A6.5 6.5 0 0 1 12 13zm0-5a11 11 0 0 1 7.8 3.2l-1.4 1.4A9 9 0 0 0 12 10a9 9 0 0 0-6.4 2.6L4.2 11.2A11 11 0 0 1 12 8z"
    ),
    # user guide / info
    "guide": _ICON(
        "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"
    ),
}
ICON_INFO = _ICON(
    "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"
)
ICON_SAVE = _ICON(
    "M17 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-4-4zM12 19a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm3-10H5V5h10v4z"
)
ICON_RESTORE = _ICON(
    "M12 5V2L8 6l4 4V7a5 5 0 1 1-4.9 6H5a7 7 0 1 0 7-8z"
)

# Short English blurbs shown under the tab bar.
TAB_INFO = {
    "stations": (
        "Configure every Voicelink, Fax, and Virtual station (name, IMEI, Wi‑Fi, SharePoint sheet). "
        "This list is the source of truth for portal lookups, fax matching, and Excel updates."
    ),
    "portal": (
        "Portal login used by the daily job to download status logs for each station IMEI "
        "(email, password, TOTP secret, and portal URL)."
    ),
    "fax": (
        "Fax service credentials, message attachment, destination numbers, and queue users. "
        "The number of fax numbers and users must match the Fax stations on the Stations tab."
    ),
    "sharepoint": (
        "Microsoft 365 / SharePoint workbook where daily results are written "
        "(tenant, site URL, and Excel file URL/name). Changing username or password "
        "clears the local Microsoft login cache so the next run signs in with the new account."
    ),
    "lab": (
        "Lab Wi‑Fi the Pi rejoins between station hops, plus the US Tailscale jump host "
        "used to reach Virtual Stations abroad."
    ),
    "guide": (
        "Full walkthrough of all settings tabs. Read a section here, then switch tabs to edit values."
    ),
}

VALID_TABS = ("stations", "portal", "fax", "sharepoint", "lab", "guide")

FIELD_LABELS = {
    "name": "Name",
    "imei": "IMEI",
    "anydesk": "Anydesk ID",
    "ssid": "WiFi SSID",
    "password": "Password",
    "access": "Access (wifi / tailscale)",
    "sheet": "SharePoint sheet",
    "sheet_alt": "Sheet name (alt)",
    "status_header": "Status column",
    "fax_user": "Fax queue user",
    "email": "Email",
    "totp_secret": "TOTP secret (2FA)",
    "portal_url": "Portal URL",
    "username": "Username",
    "url": "URL",
    "attach": "Attach file path",
    "tenant": "Tenant",
    "site_url": "Site URL",
    "file_url": "Excel file URL",
    "file_name": "Excel file name",
    "token_cache": "Token cache file",
    "lab_ssid_5g": "Lab WiFi 5 GHz",
    "lab_ssid_24g": "Lab WiFi 2.4 GHz",
    "lab_password": "Lab WiFi password",
    "jump_host": "US jump host (Tailscale IP)",
    "jump_user": "US jump SSH user",
    "cursor_api_key": "Cursor API key",
}


def esc(v: str) -> str:
    return html.escape(v or "", quote=True)


class UploadedFile:
    """Minimal file field compatible with resolve_fax_attach()."""

    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self.file = io.BytesIO(data)


class FormData:
    """Multipart form values without the removed stdlib cgi module."""

    def __init__(self) -> None:
        self._values: dict[str, list[str]] = {}
        self._files: dict[str, UploadedFile] = {}

    def add_value(self, name: str, value: str) -> None:
        self._values.setdefault(name, []).append(value)

    def add_file(self, name: str, uploaded: UploadedFile) -> None:
        self._files[name] = uploaded

    def keys(self) -> list[str]:
        return list(dict.fromkeys([*self._values.keys(), *self._files.keys()]))

    def __contains__(self, key: object) -> bool:
        return key in self._values or key in self._files

    def __getitem__(self, key: str):
        if key in self._files:
            return self._files[key]
        return self._values[key]

    def getvalue(self, key: str, default: str | None = None):
        if key in self._files:
            return default
        vals = self._values.get(key)
        if not vals:
            return default
        return vals[0] if len(vals) == 1 else vals


def fget(form, key: str, default: str = "") -> str:
    """Read a text field from parse_qs dict or FormData."""
    if isinstance(form, dict):
        vals = form.get(key)
        if not vals:
            return default
        return (vals[0] if isinstance(vals, list) else str(vals)).strip()
    if isinstance(form, FormData):
        if key in form._files:
            return default
        val = form.getvalue(key)
        if val is None:
            return default
        return str(val).strip()
    if key not in form:
        return default
    item = form[key]
    if isinstance(item, list):
        item = item[0]
    if getattr(item, "filename", None):
        return default
    val = form.getvalue(key)
    if val is None:
        return default
    return str(val).strip()


def form_keys(form) -> list[str]:
    if isinstance(form, dict):
        return list(form.keys())
    return list(form.keys())


def friendly_label(key: str) -> str:
    return FIELD_LABELS.get(key, key.replace("_", " ").title())


def safe_attach_name(filename: str) -> str:
    base = os.path.basename(filename or "").strip()
    base = re.sub(r"[^\w.\- ]+", "_", base)
    return base or "upload.bin"


def attach_mode_block(current_attach: str) -> str:
    cur = (current_attach or DEFAULT_FAX_ATTACH).replace("\\", "/")
    is_default = cur == DEFAULT_FAX_ATTACH or cur.endswith("/fax_message.txt")
    default_checked = "checked" if is_default else ""
    upload_checked = "" if is_default else "checked"
    return f"""
        <h3 class="section-title" style="margin-top:18px">Fax attachment</h3>
        <p class="hint">
          <strong>Note:</strong> uploaded file must be <code>.txt</code> or <code>.pdf</code>
          and smaller than <strong>100 MB</strong>.
          Choose the Raspberry default to keep using <code>{esc(DEFAULT_FAX_ATTACH)}</code>
          (regenerated each monitor run), or upload your own file.
        </p>
        <input type="hidden" name="fax_attach_current" value="{esc(cur)}">
        <div class="attach-box">
          <label class="radio-row">
            <input type="radio" name="fax_attach_mode" value="default" {default_checked}
                   onchange="toggleAttachUpload()">
            <span>Use Raspberry default (<code>{esc(DEFAULT_FAX_ATTACH)}</code>)</span>
          </label>
          <label class="radio-row">
            <input type="radio" name="fax_attach_mode" value="upload" {upload_checked}
                   onchange="toggleAttachUpload()">
            <span>Upload a file (.txt or .pdf, max 100 MB)</span>
          </label>
          <div id="fax-attach-upload-wrap" class="attach-upload" {'hidden' if is_default else ''}>
            <input type="file" name="fax_attach_file" id="fax_attach_file"
                   accept=".txt,.pdf,text/plain,application/pdf">
            <p class="hint" style="margin:8px 0 0">Current file: <code>{esc(cur)}</code></p>
          </div>
        </div>
    """


def is_secret(key: str) -> bool:
    k = key.lower()
    return "password" in k or "secret" in k or "totp" in k or k.endswith("_key")


def split_csv(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


def input_row(name: str, value: str, secret: bool = False, label: str | None = None) -> str:
    label_txt = esc(label or name)
    if secret:
        return f"""
        <label class="field">
          <span class="field-label">{label_txt}</span>
          <div class="pw-wrap">
            <input type="password" name="{esc(name)}" value="{esc(value)}"
                   autocomplete="off" spellcheck="false">
            <button type="button" class="eye-btn" aria-label="Show password"
                    onclick="togglePw(this)" title="Show password">
              <svg class="icon-eye" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path fill="currentColor" d="M12 5c-7 0-10 7-10 7s3 7 10 7 10-7 10-7-3-7-10-7zm0 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/>
              </svg>
              <svg class="icon-eye-off" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path fill="currentColor" d="M2.1 3.5 3.5 2.1l18.4 18.4-1.4 1.4-3.1-3.1A12.6 12.6 0 0 1 12 19c-7 0-10-7-10-7a20.3 20.3 0 0 1 5.2-5.5L2.1 3.5zM12 7a5 5 0 0 1 5 5c0 .6-.1 1.1-.3 1.6l-6.3-6.3c.5-.2 1-.3 1.6-.3zm-7.5 5s2.2 4.5 7.5 4.5c.9 0 1.7-.1 2.4-.4l-1.6-1.6A3 3 0 0 1 9.5 11L6.8 8.3A17 17 0 0 0 4.5 12z"/>
              </svg>
            </button>
          </div>
        </label>"""
    return f"""
        <label class="field">
          <span class="field-label">{label_txt}</span>
          <input type="text" name="{esc(name)}" value="{esc(value)}"
                 autocomplete="off" spellcheck="false">
        </label>"""


def station_type_of(st: dict[str, str]) -> str:
    name = (st.get("name") or "").lower()
    status = (st.get("status_header") or "").strip()
    access = (st.get("access") or "").lower()
    if "fax" in name or status == "Fax Status":
        return "fax"
    if "virtual" in name or access == "tailscale" or status == "":
        if "voicelink" in name:
            return "voicelink"
        if "virtual" in name or access == "tailscale":
            return "virtual"
    if "voicelink" in name or status == "Voicelink Status":
        return "voicelink"
    if access == "tailscale":
        return "virtual"
    return "voicelink"


def station_type_icon(stype: str) -> str:
    """Small type badge icon (desk phone / fax / cloud)."""
    # desk phone: cradle handset + body with keypad
    phone = (
        '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">'
        '<path fill="currentColor" d="M6 3.75c0-.97.78-1.75 1.75-1.75h8.5c.97 0 1.75.78 1.75 1.75V7H6V3.75z"/>'
        '<path fill="currentColor" d="M4.5 8h15v9.25A2.75 2.75 0 0 1 16.75 20h-9.5A2.75 2.75 0 0 1 4.5 17.25V8zm3 3.1v1.7h1.7v-1.7H7.5zm3.9 0v1.7h1.7v-1.7h-1.7zm3.9 0v1.7h1.7v-1.7h-1.7zm-7.8 3.6v1.7h1.7v-1.7H7.5zm3.9 0v1.7h1.7v-1.7h-1.7zm3.9 0v1.7h1.7v-1.7h-1.7z"/>'
        "</svg>"
    )
    fax = (
        '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">'
        '<path fill="currentColor" d="M6 2h8l4 4v3h-2V7h-3V4H7v4H5V4a2 2 0 0 1 2-2zm-1 9h14a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2zm2 3v4h10v-4H7z"/>'
        "</svg>"
    )
    virtual = (
        '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">'
        '<path fill="currentColor" d="M19.35 10.04A7.49 7.49 0 0 0 5.05 9.3 5.5 5.5 0 0 0 5.5 20h13.1A4.4 4.4 0 0 0 23 15.6a4.39 4.39 0 0 0-3.65-5.56z"/>'
        "</svg>"
    )
    if stype == "fax":
        return fax
    if stype == "virtual":
        return virtual
    return phone


def station_card(i: int, st: dict[str, str]) -> str:
    stype = station_type_of(st)
    status = st.get("status_header")
    if status is None or status == "":
        status = default_status_header(st.get("name") or "")
        if stype == "virtual":
            status = ""
        elif stype == "fax":
            status = "Fax Status"
        elif stype == "voicelink":
            status = "Voicelink Status"

    fields = [
        ("name", False),
        ("imei", False),
        ("anydesk", False),
        ("ssid", False),
        ("password", True),
        ("access", False),
        ("sheet", False),
    ]
    if stype == "fax":
        fields.append(("fax_user", False))

    rows = []
    for key, secret in fields:
        val = st.get(key) or ""
        if key == "sheet" and not val:
            val = st.get("name") or ""
        rows.append(
            input_row(f"st_{i}_{key}", val, secret=secret, label=friendly_label(key))
        )
    title = esc(st.get("name") or f"Station {i}")
    type_label = {
        "voicelink": "Voicelink Station",
        "fax": "Fax Station",
        "virtual": "Virtual Station",
    }.get(stype, stype)
    return f"""
    <article class="station-card" data-station-index="{i}" data-station-type="{stype}" id="station-card-{i}">
      <header class="station-head">
        <h3 class="station-title">{title}</h3>
        <div class="station-head-right">
          <span class="type-badge type-{stype}" title="{type_label}">
            {station_type_icon(stype)}
            <span class="type-badge-text">{type_label}</span>
          </span>
          <button type="button" class="icon-btn danger" title="Delete station"
                  onclick="markDeleteStation({i})" aria-label="Delete station">✕</button>
        </div>
      </header>
      <input type="hidden" name="st_{i}_delete" id="st_{i}_delete" value="">
      <input type="hidden" name="st_{i}_station_type" value="{esc(stype)}">
      <input type="hidden" name="st_{i}_status_header" value="{esc(status)}">
      <input type="hidden" name="st_{i}_sheet_alt" value="">
      <div class="grid">{"".join(rows)}</div>
      <p class="delete-hint" id="st_{i}_hint" hidden>Marked for deletion — click Save all to remove, or Restore Default Settings to undo.</p>
    </article>"""


def fields_block(prefix: str, keys: tuple[str, ...], cfg: dict[str, str]) -> str:
    return "".join(
        input_row(
            f"{prefix}_{key}",
            cfg.get(key) or "",
            secret=is_secret(key),
            label=friendly_label(key),
        )
        for key in keys
    )


def fax_number_rows(numbers: list[str]) -> str:
    if not numbers:
        numbers = ["", ""]
    rows = []
    for i, num in enumerate(numbers):
        n = i + 1
        rows.append(
            f"""
            <div class="list-row" data-fax-number-row="{i}">
              <label class="field grow">
                <span class="field-label">Fax number — Fax Station No. {n}</span>
                <input type="text" name="fax_number_{i}" value="{esc(num)}" autocomplete="off">
              </label>
              <button type="button" class="icon-btn danger" title="Remove"
                      onclick="removeListRow(this)" aria-label="Remove fax number">✕</button>
            </div>"""
        )
    return "".join(rows)


def fax_user_rows(pairs: list[str]) -> str:
    if not pairs:
        pairs = [":", ":"]
    rows = []
    for i, pair in enumerate(pairs):
        n = i + 1
        if ":" in pair:
            user, imei = pair.split(":", 1)
        else:
            user, imei = pair, ""
        rows.append(
            f"""
            <div class="list-row" data-fax-user-row="{i}">
              <label class="field grow">
                <span class="field-label">Fax user — Fax Station No. {n}</span>
                <input type="text" name="fax_user_name_{i}" value="{esc(user.strip())}"
                       placeholder="e.g. simplifivn1" autocomplete="off">
              </label>
              <label class="field grow">
                <span class="field-label">IMEI — Fax Station No. {n}</span>
                <input type="text" name="fax_user_imei_{i}" value="{esc(imei.strip())}"
                       placeholder="IMEI" autocomplete="off">
              </label>
              <button type="button" class="icon-btn danger" title="Remove"
                      onclick="removeListRow(this)" aria-label="Remove fax user">✕</button>
            </div>"""
        )
    return "".join(rows)


def page(message: str = "", active_tab: str = "stations", error: str = "") -> bytes:
    monitor = STATE["monitor_conf"]
    portal = STATE["portal_conf"]
    fax = STATE["fax_conf"]
    share = STATE["sharepoint_conf"]
    stations = load_stations(monitor)
    portal_cfg = load_conf(portal)
    fax_cfg = load_conf(fax)
    sp_cfg = load_conf(share)
    mon_cfg = load_conf(monitor)

    tab = active_tab if active_tab in VALID_TABS else "stations"
    tab_info_json = json.dumps(TAB_INFO, ensure_ascii=False)
    initial_info = esc(TAB_INFO.get(tab, ""))
    station_html = "".join(station_card(i, st) for i, st in enumerate(stations))
    if not stations:
        station_html = '<p class="empty" id="stations-empty">No stations yet — use + to add one.</p>'

    numbers = split_csv(fax_cfg.get("fax_numbers") or "")
    users = split_csv(fax_cfg.get("fax_users") or "")
    # Prefer at least as many rows as current Fax stations
    fax_station_count = sum(
        1
        for st in stations
        if "fax" in (st.get("name") or "").lower()
        or (st.get("status_header") or "") == "Fax Status"
    )
    while len(numbers) < max(2, fax_station_count):
        numbers.append("")
    while len(users) < max(2, fax_station_count):
        users.append("")

    msg = ""
    if error:
        msg = f'<div class="toast err" role="alert">{esc(error)}</div>'
    elif message:
        msg = f'<div class="toast ok" role="status">{esc(message)}</div>'

    def tab_btn(tid: str, label: str) -> str:
        on = " active" if tab == tid else ""
        return (
            f'<button type="button" class="tab{on}" data-tab="{tid}" '
            f'onclick="showTab(\'{tid}\')">'
            f'<span class="tab-icon" aria-hidden="true">{TAB_ICONS.get(tid, "")}</span>'
            f'<span>{label}</span></button>'
        )

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Simplifi monitor config</title>
<style>
:root {{
  --bg: #eef2f7;
  --card: #ffffff;
  --ink: #15202b;
  --muted: #5b6b7c;
  --line: #d5deea;
  --accent: #1a6fd4;
  --accent-2: #0f4f9e;
  --ok-bg: #e8f7ee;
  --ok-line: #9ed4b2;
  --err-bg: #fdecea;
  --err-line: #f5c2c0;
  --danger: #b42318;
  --shadow: 0 10px 30px rgba(21,32,43,.08);
  --radius: 14px;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  color: var(--ink);
  background:
    radial-gradient(1200px 500px at 10% -10%, #d9e8ff 0%, transparent 55%),
    radial-gradient(900px 400px at 100% 0%, #e7f3ff 0%, transparent 50%),
    var(--bg);
  min-height: 100vh;
}}
.wrap {{ width: min(980px, calc(100% - 28px)); margin: 28px auto 48px; }}
.hero {{
  background: linear-gradient(135deg, #143a6b, #1a6fd4 55%, #3d93e8);
  color: #fff; border-radius: calc(var(--radius) + 4px);
  padding: 22px 24px; box-shadow: var(--shadow);
}}
.hero h1 {{ margin: 0; font-size: clamp(1.25rem, 3vw, 1.7rem); }}
.tabs {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 18px 0 14px; }}
.tab {{
  border: 1px solid var(--line); background: rgba(255,255,255,.72); color: var(--ink);
  border-radius: 999px; padding: 10px 16px; font-weight: 600; cursor: pointer;
  display: inline-flex; align-items: center; gap: 8px;
  transition: transform .15s ease, background .15s ease, border-color .15s ease, box-shadow .15s ease;
}}
.tab-icon, .btn-icon {{
  display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
  line-height: 0;
}}
.tab-icon svg, .btn-icon svg {{ display: block; }}
.tab:hover {{ transform: translateY(-1px); border-color: #9db8de; box-shadow: 0 6px 16px rgba(26,111,212,.12); }}
.tab.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
.tab-info {{
  display: flex; align-items: flex-start; gap: 10px;
  margin: 0 0 14px; padding: 12px 14px;
  background: #e8f1fc; border: 1px solid #c5daf5; border-radius: 12px;
  color: #1e3a5f; font-size: .92rem; line-height: 1.45;
}}
.tab-info-icon {{ flex-shrink: 0; color: var(--accent); margin-top: 1px; }}
.tab-info-icon svg {{ width: 20px; height: 20px; display: block; }}
.tab-info-text {{ margin: 0; flex: 1; }}
.panel {{ display: none; animation: fade .18s ease; }}
.panel.active {{ display: block; }}
@keyframes fade {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: none; }} }}
.card, .station-card {{
  background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 16px; margin: 0 0 14px; box-shadow: var(--shadow);
  transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease, opacity .2s ease;
}}
.station-card:hover, .card:hover {{
  transform: translateY(-2px); border-color: #b7c9e2; box-shadow: 0 14px 34px rgba(21,32,43,.12);
}}
.station-card.marked-delete {{
  opacity: .45; filter: grayscale(.35); border-color: #f0a8a4;
  transform: none; box-shadow: none;
}}
.station-head {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }}
.station-head-right {{ display: flex; align-items: center; gap: 8px; }}
.station-head h3 {{ margin: 0; font-size: 1.05rem; }}
.badge {{
  background: #eaf2ff; color: var(--accent-2); border-radius: 999px;
  padding: 4px 10px; font-size: .8rem; font-weight: 700;
}}
.type-badge {{
  display: inline-flex; align-items: center; gap: 6px;
  height: 30px; padding: 0 10px 0 8px; border-radius: 999px;
  color: var(--accent-2); background: #eaf2ff;
  font-size: .78rem; font-weight: 700; white-space: nowrap;
}}
.type-badge.type-fax {{ color: #9a3412; background: #ffedd5; }}
.type-badge.type-virtual {{ color: #5b21b6; background: #ede9fe; }}
.type-badge.type-voicelink {{ color: #1d4ed8; background: #dbeafe; }}
.type-badge svg {{ display: block; width: 16px; height: 16px; flex-shrink: 0; }}
.type-badge-text {{ line-height: 1; }}
.grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 14px; }}
.field {{ display: block; margin: 0; }}
.field.grow {{ flex: 1; min-width: 140px; }}
.field-label {{
  display: block; margin-bottom: 6px; color: var(--muted);
  font-size: .82rem; font-weight: 600;
}}
input[type=text], input[type=password] {{
  width: 100%; border: 1px solid var(--line); border-radius: 10px;
  padding: 11px 12px; font-size: 0.98rem; background: #fbfcfe;
  transition: border-color .15s ease, box-shadow .15s ease, background .15s ease;
}}
input[type=text]:hover, input[type=password]:hover {{ border-color: #a9bdd9; background: #fff; }}
input[type=text]:focus, input[type=password]:focus {{
  outline: none; border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(26,111,212,.18); background: #fff;
}}
.pw-wrap {{ position: relative; }}
.pw-wrap input {{ padding-right: 46px; }}
.eye-btn, .icon-btn {{
  border: 0; background: transparent; color: var(--muted); width: 36px; height: 36px;
  border-radius: 8px; cursor: pointer; display: grid; place-items: center;
  transition: background .15s ease, color .15s ease, transform .15s ease;
  font-size: 1.1rem; line-height: 1;
}}
.eye-btn {{ position: absolute; right: 6px; top: 50%; transform: translateY(-50%); }}
.eye-btn:hover, .icon-btn:hover {{ background: #eaf2ff; color: var(--accent-2); }}
.icon-btn.danger:hover {{ background: #fdecea; color: var(--danger); }}
.eye-btn .icon-eye-off {{ display: none; }}
.eye-btn.is-visible .icon-eye {{ display: none; }}
.eye-btn.is-visible .icon-eye-off {{ display: block; }}
.list-block {{ display: flex; flex-direction: column; gap: 10px; margin-top: 8px; }}
.list-row {{
  display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap;
  padding: 10px; border: 1px dashed var(--line); border-radius: 10px; background: #fbfcfe;
}}
.add-row {{
  display: inline-flex; align-items: center; gap: 8px; margin-top: 10px;
  border: 1px dashed #9db8de; background: #f3f8ff; color: var(--accent-2);
  border-radius: 10px; padding: 10px 14px; font-weight: 700; cursor: pointer;
  transition: background .15s ease, transform .15s ease;
}}
.add-row:hover {{ background: #e7f0ff; transform: translateY(-1px); }}
.section-title {{ margin: 4px 0 0; font-size: 1rem; }}
.hint {{ color: var(--muted); font-size: .88rem; margin: 4px 0 10px; }}
.guide-card h2 {{ margin: 0 0 10px; font-size: 1.2rem; }}
.guide-card h3 {{
  margin: 18px 0 6px; font-size: 1.02rem; color: var(--accent-2);
  display: flex; align-items: center; gap: 8px;
}}
.guide-card h3:first-of-type {{ margin-top: 8px; }}
.guide-card p, .guide-card li {{ color: var(--muted); font-size: .94rem; line-height: 1.5; }}
.guide-card ul {{ margin: 6px 0 0; padding-left: 1.2rem; }}
.guide-card li {{ margin: 4px 0; }}
.attach-box {{
  border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px;
  background: #fbfcfe; display: flex; flex-direction: column; gap: 10px;
}}
.radio-row {{
  display: flex; align-items: flex-start; gap: 10px; cursor: pointer; font-size: .95rem;
}}
.radio-row input {{ margin-top: 3px; width: auto; }}
.attach-upload input[type=file] {{
  width: 100%; border: 1px dashed #9db8de; border-radius: 10px;
  padding: 12px; background: #f3f8ff;
}}
.delete-hint {{ color: var(--danger); font-size: .88rem; margin: 10px 0 0; }}
.empty {{ color: var(--muted); }}
.actions {{
  position: sticky; bottom: 0; display: flex; flex-wrap: wrap; gap: 10px;
  padding: 14px 0 4px; background: linear-gradient(to top, var(--bg) 70%, transparent);
}}
.actions.is-hidden {{ display: none; }}
.btn {{
  border: 0; border-radius: 10px; padding: 12px 18px; font-size: 1rem; font-weight: 700;
  cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  transition: transform .15s ease, box-shadow .15s ease, background .15s ease;
}}
.btn:hover {{ transform: translateY(-1px); }}
.btn-primary {{ background: var(--accent); color: #fff; box-shadow: 0 8px 18px rgba(26,111,212,.25); }}
.btn-primary:hover {{ background: var(--accent-2); }}
.btn-secondary {{ background: #5f6b7a; color: #fff; }}
.btn-secondary:hover {{ background: #4a5563; }}
.toast {{ margin: 14px 0 0; padding: 12px 14px; border-radius: 10px; }}
.toast.ok {{ border: 1px solid var(--ok-line); background: var(--ok-bg); }}
.toast.err {{ border: 1px solid var(--err-line); background: var(--err-bg); color: #7a1c16; }}
.modal-backdrop {{
  position: fixed; inset: 0; background: rgba(15,23,35,.45);
  display: none; align-items: center; justify-content: center; padding: 16px; z-index: 50;
}}
.modal-backdrop.open {{ display: flex; }}
.modal {{
  width: min(420px, 100%); background: #fff; border-radius: 14px; padding: 18px;
  box-shadow: 0 20px 50px rgba(0,0,0,.25);
}}
.modal h2 {{ margin: 0 0 8px; font-size: 1.15rem; }}
.modal p {{ margin: 0 0 14px; color: var(--muted); }}
.modal-actions {{ display: flex; flex-direction: column; gap: 8px; }}
.modal-actions button {{
  border: 1px solid var(--line); background: #f7faff; border-radius: 10px;
  padding: 12px; font-weight: 700; cursor: pointer; text-align: left;
}}
.modal-actions button:hover {{ border-color: var(--accent); background: #eaf2ff; }}
.modal-cancel {{ margin-top: 8px; background: transparent !important; color: var(--muted); border: 0 !important; }}
@media (max-width: 720px) {{
  .wrap {{ width: calc(100% - 20px); margin: 16px auto 36px; }}
  .grid {{ grid-template-columns: 1fr; }}
  .tab {{ flex: 1 1 calc(50% - 8px); text-align: center; }}
  .actions {{ flex-direction: column; }}
  .btn {{ width: 100%; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header class="hero"><h1>Simplifi Daily Monitor Settings</h1></header>
  {msg}
  <form method="POST" action="/save" id="cfg-form" enctype="multipart/form-data"
        onsubmit="return validateBeforeSave(event)">
    <input type="hidden" name="active_tab" id="active_tab" value="{esc(tab)}">
    <input type="hidden" name="add_station_type" id="add_station_type" value="">
    <nav class="tabs" aria-label="Config sections">
      {tab_btn("stations", "Stations")}
      {tab_btn("portal", "Portal")}
      {tab_btn("fax", "Fax")}
      {tab_btn("sharepoint", "SharePoint")}
      {tab_btn("lab", "Lab / Jump")}
      {tab_btn("guide", "User Guide")}
    </nav>

    <div class="tab-info" id="tab-info" role="note">
      <span class="tab-info-icon" aria-hidden="true">{ICON_INFO}</span>
      <p class="tab-info-text" id="tab-info-text">{initial_info}</p>
    </div>

    <section class="panel{' active' if tab == 'stations' else ''}" id="tab-stations">
      <div class="card">
        <div id="stations-list">{station_html}</div>
        <button type="button" class="add-row" onclick="openAddStationModal()">＋ Add station</button>
      </div>
    </section>

    <section class="panel{' active' if tab == 'portal' else ''}" id="tab-portal">
      <div class="card"><div class="grid">{fields_block("portal", PORTAL_KEYS, portal_cfg)}</div></div>
    </section>

    <section class="panel{' active' if tab == 'fax' else ''}" id="tab-fax">
      <div class="card">
        <div class="grid">{fields_block("fax", FAX_BASIC_KEYS, fax_cfg)}</div>
        {attach_mode_block(fax_cfg.get("attach") or DEFAULT_FAX_ATTACH)}
        <h3 class="section-title" style="margin-top:18px">Fax numbers</h3>
        <p class="hint">One number per Fax Station. Use ＋ to add Fax Station No. 3, …</p>
        <div class="list-block" id="fax-numbers-list">{fax_number_rows(numbers)}</div>
        <button type="button" class="add-row" onclick="addFaxNumberRow()">＋ Add fax number</button>

        <h3 class="section-title" style="margin-top:18px">Fax users</h3>
        <p class="hint">One queue user + IMEI per Fax Station. Count must match Fax stations on the Stations tab.</p>
        <div class="list-block" id="fax-users-list">{fax_user_rows(users)}</div>
        <button type="button" class="add-row" onclick="addFaxUserRow()">＋ Add fax user</button>
      </div>
    </section>

    <section class="panel{' active' if tab == 'sharepoint' else ''}" id="tab-sharepoint">
      <div class="card"><div class="grid">{fields_block("sharepoint", SHAREPOINT_KEYS, sp_cfg)}</div></div>
    </section>

    <section class="panel{' active' if tab == 'lab' else ''}" id="tab-lab">
      <div class="card"><div class="grid">{fields_block("monitor", MONITOR_KEYS, mon_cfg)}</div></div>
    </section>

    <section class="panel{' active' if tab == 'guide' else ''}" id="tab-guide">
      <div class="card guide-card">
        <h2>How to use this settings page</h2>
        <p>Open a tab, change values, then click <strong>Save all</strong>. Use <strong>Restore Default Settings</strong> only if you want to reload values from the saved config files on disk (unsaved edits are discarded).</p>

        <h3><span class="tab-icon">{TAB_ICONS["stations"]}</span> Stations</h3>
        <p>Master list of routers/stations the daily monitor checks.</p>
        <ul>
          <li><strong>Voicelink Station</strong> — local Wi‑Fi hop + SSH to the router; status goes to a SharePoint sheet.</li>
          <li><strong>Fax Station</strong> — same as Voicelink, plus a Fax queue user used by the Fax tab.</li>
          <li><strong>Virtual Station</strong> — reached via the US Tailscale jump host (Lab / Jump tab); access is usually <code>tailscale</code>.</li>
          <li>Use <strong>＋ Add station</strong> to create a card, fill Name / IMEI / Wi‑Fi / SharePoint sheet, then Save all.</li>
          <li>Mark delete with ✕, then Save all to remove. Status column defaults are applied automatically.</li>
        </ul>

        <h3><span class="tab-icon">{TAB_ICONS["portal"]}</span> Portal</h3>
        <p>Credentials for the Simplifi web portal. The monitor logs in (including 2FA/TOTP when configured) and downloads per‑IMEI logs for stations defined on the Stations tab.</p>
        <ul>
          <li>Keep email, password, TOTP secret, and portal URL up to date.</li>
          <li>IMEI values themselves live on the Stations tab — not here.</li>
        </ul>

        <h3><span class="tab-icon">{TAB_ICONS["fax"]}</span> Fax</h3>
        <p>Sends the daily test fax and checks the queue for each Fax Station.</p>
        <ul>
          <li>Set username/password/URL for the fax service.</li>
          <li>Attachment: keep the default text file, or upload a <code>.txt</code> / <code>.pdf</code> (max 100&nbsp;MB).</li>
          <li>Add one fax number and one fax user (+ IMEI) per Fax Station. Counts must match before Save all succeeds.</li>
        </ul>

        <h3><span class="tab-icon">{TAB_ICONS["sharepoint"]}</span> SharePoint</h3>
        <p>Where daily PASS/FAIL (and related cells) are written in Excel Online.</p>
        <ul>
          <li>Fill tenant, site URL, and Excel file URL/name.</li>
          <li>Sheet names come from each station’s SharePoint sheet field on the Stations tab.</li>
          <li>If you change the SharePoint username or password and Save all, the Pi deletes <code>token_cache.bin</code> automatically so the next run logs in with the new account.</li>
        </ul>

        <h3><span class="tab-icon">{TAB_ICONS["lab"]}</span> Lab / Jump</h3>
        <p>Network settings for the Raspberry Pi and the US laptop used for Virtual Stations.</p>
        <ul>
          <li><strong>Lab Wi‑Fi</strong> — SSIDs/password the Pi reconnects to between station Wi‑Fi hops.</li>
          <li><strong>Jump host</strong> — Tailscale IP and SSH user of the US machine that hops to Virtual Station routers.</li>
          <li>Optional Cursor API key is only used if log analysis is enabled in <code>monitor.conf</code>.</li>
        </ul>
      </div>
    </section>

    <div class="actions{' is-hidden' if tab == 'guide' else ''}" id="cfg-actions">
      <button type="submit" class="btn btn-primary">
        <span class="btn-icon" aria-hidden="true">{ICON_SAVE}</span>
        <span>Save all</span>
      </button>
      <button type="submit" name="restore_defaults" value="1" class="btn btn-secondary" formnovalidate>
        <span class="btn-icon" aria-hidden="true">{ICON_RESTORE}</span>
        <span>Restore Default Settings</span>
      </button>
    </div>
  </form>
</div>

<div class="modal-backdrop" id="add-station-modal" onclick="backdropClose(event)">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="add-station-title">
    <h2 id="add-station-title">Add station</h2>
    <p>Choose a station type. An empty card will be added — fill it in, then Save all.</p>
    <div class="modal-actions">
      <button type="button" onclick="chooseStationType('voicelink')">Voicelink Station</button>
      <button type="button" onclick="chooseStationType('fax')">Fax Station</button>
      <button type="button" onclick="chooseStationType('virtual')">Virtual Station</button>
      <button type="button" class="modal-cancel" onclick="closeAddStationModal()">Cancel</button>
    </div>
  </div>
</div>

<script>
const TAB_INFO = {tab_info_json};
function showTab(id) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const panel = document.getElementById('tab-' + id);
  if (panel) panel.classList.add('active');
  document.querySelectorAll('.tab[data-tab="' + id + '"]').forEach(t => t.classList.add('active'));
  const hidden = document.getElementById('active_tab');
  if (hidden) hidden.value = id;
  const info = document.getElementById('tab-info-text');
  if (info) info.textContent = TAB_INFO[id] || '';
  const actions = document.getElementById('cfg-actions');
  if (actions) actions.classList.toggle('is-hidden', id === 'guide');
  try {{ history.replaceState(null, '', '#' + id); }} catch (e) {{}}
}}
function togglePw(btn) {{
  const wrap = btn.closest('.pw-wrap');
  const input = wrap.querySelector('input');
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  btn.classList.toggle('is-visible', show);
  btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  btn.title = show ? 'Hide password' : 'Show password';
}}
function markDeleteStation(i) {{
  const card = document.getElementById('station-card-' + i);
  const hidden = document.getElementById('st_' + i + '_delete');
  const hint = document.getElementById('st_' + i + '_hint');
  if (!card || !hidden) return;
  if (hidden.value === '1') {{
    if (!confirm('This station is marked for deletion. Undo the delete mark?')) return;
    hidden.value = '';
    card.classList.remove('marked-delete');
    if (hint) hint.hidden = true;
    return;
  }}
  if (!confirm('Are you sure you want to delete this station?')) return;
  hidden.value = '1';
  card.classList.add('marked-delete');
  if (hint) hint.hidden = false;
}}
function openAddStationModal() {{
  document.getElementById('add-station-modal').classList.add('open');
}}
function closeAddStationModal() {{
  document.getElementById('add-station-modal').classList.remove('open');
}}
function backdropClose(ev) {{
  if (ev.target.id === 'add-station-modal') closeAddStationModal();
}}
function nextStationIndex() {{
  let max = -1;
  document.querySelectorAll('[data-station-index]').forEach(el => {{
    const n = parseInt(el.getAttribute('data-station-index'), 10);
    if (!isNaN(n) && n > max) max = n;
  }});
  return max + 1;
}}
function stationTypeIcon(type) {{
  if (type === 'fax') {{
    return `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M6 2h8l4 4v3h-2V7h-3V4H7v4H5V4a2 2 0 0 1 2-2zm-1 9h14a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2zm2 3v4h10v-4H7z"/></svg>`;
  }}
  if (type === 'virtual') {{
    return `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M19.35 10.04A7.49 7.49 0 0 0 5.05 9.3 5.5 5.5 0 0 0 5.5 20h13.1A4.4 4.4 0 0 0 23 15.6a4.39 4.39 0 0 0-3.65-5.56z"/></svg>`;
  }}
  return `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M6 3.75c0-.97.78-1.75 1.75-1.75h8.5c.97 0 1.75.78 1.75 1.75V7H6V3.75z"/><path fill="currentColor" d="M4.5 8h15v9.25A2.75 2.75 0 0 1 16.75 20h-9.5A2.75 2.75 0 0 1 4.5 17.25V8zm3 3.1v1.7h1.7v-1.7H7.5zm3.9 0v1.7h1.7v-1.7h-1.7zm3.9 0v1.7h1.7v-1.7h-1.7zm-7.8 3.6v1.7h1.7v-1.7H7.5zm3.9 0v1.7h1.7v-1.7h-1.7zm3.9 0v1.7h1.7v-1.7h-1.7z"/></svg>`;
}}
function stationTypeLabel(type) {{
  if (type === 'fax') return 'Fax Station';
  if (type === 'virtual') return 'Virtual Station';
  return 'Voicelink Station';
}}
function stationTemplate(i, type) {{
  let name = '', status = '', access = 'wifi';
  if (type === 'voicelink') {{
    name = 'Voicelink Station No. ';
    status = 'Voicelink Status';
  }} else if (type === 'fax') {{
    name = 'Fax Station No. ';
    status = 'Fax Status';
  }} else {{
    name = 'Virtual Station No. ';
    status = '';
    access = 'tailscale';
  }}
  const typeLabel = stationTypeLabel(type);
  const fields = [
    ['name', name, false],
    ['imei', '', false],
    ['anydesk', '', false],
    ['ssid', '', false],
    ['password', '', true],
    ['access', access, false],
    ['sheet', name, false],
  ];
  if (type === 'fax') fields.push(['fax_user', '', false]);
  const labels = {{
    name:'Name', imei:'IMEI', anydesk:'Anydesk ID', ssid:'WiFi SSID', password:'Password',
    access:'Access (wifi / tailscale)', sheet:'SharePoint sheet', fax_user:'Fax queue user'
  }};
  let grid = '';
  for (const [key, val, secret] of fields) {{
    const label = labels[key] || key;
    if (secret) {{
      grid += `<label class="field"><span class="field-label">${{label}}</span>
        <div class="pw-wrap">
          <input type="password" name="st_${{i}}_${{key}}" value="${{val}}" autocomplete="off">
          <button type="button" class="eye-btn" onclick="togglePw(this)" title="Show password" aria-label="Show password">
            <svg class="icon-eye" viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 5c-7 0-10 7-10 7s3 7 10 7 10-7 10-7-3-7-10-7zm0 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/></svg>
            <svg class="icon-eye-off" viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M2.1 3.5 3.5 2.1l18.4 18.4-1.4 1.4-3.1-3.1A12.6 12.6 0 0 1 12 19c-7 0-10-7-10-7a20.3 20.3 0 0 1 5.2-5.5L2.1 3.5zM12 7a5 5 0 0 1 5 5c0 .6-.1 1.1-.3 1.6l-6.3-6.3c.5-.2 1-.3 1.6-.3zm-7.5 5s2.2 4.5 7.5 4.5c.9 0 1.7-.1 2.4-.4l-1.6-1.6A3 3 0 0 1 9.5 11L6.8 8.3A17 17 0 0 0 4.5 12z"/></svg>
          </button>
        </div></label>`;
    }} else {{
      grid += `<label class="field"><span class="field-label">${{label}}</span>
        <input type="text" name="st_${{i}}_${{key}}" value="${{val}}" autocomplete="off"></label>`;
    }}
  }}
  return `<article class="station-card" data-station-index="${{i}}" data-station-type="${{type}}" id="station-card-${{i}}">
    <header class="station-head">
      <h3 class="station-title">New ${{typeLabel}}</h3>
      <div class="station-head-right">
        <span class="type-badge type-${{type}}" title="${{typeLabel}}">${{stationTypeIcon(type)}}<span class="type-badge-text">${{typeLabel}}</span></span>
        <button type="button" class="icon-btn danger" title="Delete station"
                onclick="markDeleteStation(${{i}})" aria-label="Delete station">✕</button>
      </div>
    </header>
    <input type="hidden" name="st_${{i}}_delete" id="st_${{i}}_delete" value="">
    <input type="hidden" name="st_${{i}}_station_type" value="${{type}}">
    <input type="hidden" name="st_${{i}}_status_header" value="${{status}}">
    <input type="hidden" name="st_${{i}}_sheet_alt" value="">
    <div class="grid">${{grid}}</div>
    <p class="delete-hint" id="st_${{i}}_hint" hidden>Marked for deletion — click Save all to remove, or Restore Default Settings to undo.</p>
  </article>`;
}}
function chooseStationType(type) {{
  const i = nextStationIndex();
  const list = document.getElementById('stations-list');
  const empty = document.getElementById('stations-empty');
  if (empty) empty.remove();
  list.insertAdjacentHTML('beforeend', stationTemplate(i, type));
  closeAddStationModal();
  showTab('stations');
  const nameInput = document.querySelector(`#station-card-${{i}} input[name="st_${{i}}_name"]`);
  if (nameInput) nameInput.focus();
}}
function renumberFaxLabels(listId, kind) {{
  const list = document.getElementById(listId);
  const rows = [...list.querySelectorAll('.list-row')];
  rows.forEach((row, idx) => {{
    const n = idx + 1;
    row.querySelectorAll('.field-label').forEach((lab, j) => {{
      if (kind === 'number') lab.textContent = `Fax number — Fax Station No. ${{n}}`;
      else if (j === 0) lab.textContent = `Fax user — Fax Station No. ${{n}}`;
      else lab.textContent = `IMEI — Fax Station No. ${{n}}`;
    }});
    row.querySelectorAll('input').forEach((inp) => {{
      if (inp.name.startsWith('fax_number_')) inp.name = `fax_number_${{idx}}`;
      if (inp.name.startsWith('fax_user_name_')) inp.name = `fax_user_name_${{idx}}`;
      if (inp.name.startsWith('fax_user_imei_')) inp.name = `fax_user_imei_${{idx}}`;
    }});
  }});
}}
function addFaxNumberRow() {{
  const list = document.getElementById('fax-numbers-list');
  const idx = list.querySelectorAll('.list-row').length;
  const n = idx + 1;
  list.insertAdjacentHTML('beforeend', `
    <div class="list-row">
      <label class="field grow">
        <span class="field-label">Fax number — Fax Station No. ${{n}}</span>
        <input type="text" name="fax_number_${{idx}}" value="" autocomplete="off">
      </label>
      <button type="button" class="icon-btn danger" onclick="removeListRow(this)" aria-label="Remove fax number">✕</button>
    </div>`);
}}
function addFaxUserRow() {{
  const list = document.getElementById('fax-users-list');
  const idx = list.querySelectorAll('.list-row').length;
  const n = idx + 1;
  list.insertAdjacentHTML('beforeend', `
    <div class="list-row">
      <label class="field grow">
        <span class="field-label">Fax user — Fax Station No. ${{n}}</span>
        <input type="text" name="fax_user_name_${{idx}}" value="" placeholder="e.g. simplifivn1" autocomplete="off">
      </label>
      <label class="field grow">
        <span class="field-label">IMEI — Fax Station No. ${{n}}</span>
        <input type="text" name="fax_user_imei_${{idx}}" value="" placeholder="IMEI" autocomplete="off">
      </label>
      <button type="button" class="icon-btn danger" onclick="removeListRow(this)" aria-label="Remove fax user">✕</button>
    </div>`);
}}
function removeListRow(btn) {{
  const row = btn.closest('.list-row');
  const list = row.parentElement;
  row.remove();
  if (list.id === 'fax-numbers-list') renumberFaxLabels('fax-numbers-list', 'number');
  if (list.id === 'fax-users-list') renumberFaxLabels('fax-users-list', 'user');
}}
function countFaxStationsInForm() {{
  let count = 0;
  document.querySelectorAll('[data-station-index]').forEach(card => {{
    const i = card.getAttribute('data-station-index');
    const del = document.getElementById('st_' + i + '_delete');
    if (del && del.value === '1') return;
    const stype = (card.getAttribute('data-station-type') || '').toLowerCase();
    const name = (card.querySelector(`input[name="st_${{i}}_name"]`) || {{value:''}}).value.trim().toLowerCase();
    const status = (card.querySelector(`input[name="st_${{i}}_status_header"]`) || {{value:''}}).value.trim();
    if (stype === 'fax' || name.includes('fax') || status === 'Fax Status') count += 1;
  }});
  return count;
}}
function filledFaxNumbers() {{
  return [...document.querySelectorAll('#fax-numbers-list input[name^="fax_number_"]')]
    .map(i => i.value.trim()).filter(Boolean).length;
}}
function filledFaxUsers() {{
  let n = 0;
  const names = [...document.querySelectorAll('#fax-users-list input[name^="fax_user_name_"]')];
  names.forEach((nameInp, idx) => {{
    const imei = document.querySelector(`#fax-users-list input[name="fax_user_imei_${{idx}}"]`);
    if (nameInp.value.trim() || (imei && imei.value.trim())) n += 1;
  }});
  return n;
}}
function toggleAttachUpload() {{
  const upload = document.querySelector('input[name="fax_attach_mode"][value="upload"]');
  const wrap = document.getElementById('fax-attach-upload-wrap');
  if (!wrap) return;
  wrap.hidden = !(upload && upload.checked);
}}
function validateBeforeSave(ev) {{
  if (ev.submitter && ev.submitter.name === 'restore_defaults') return true;
  const mode = document.querySelector('input[name="fax_attach_mode"]:checked');
  if (mode && mode.value === 'upload') {{
    const fileInput = document.getElementById('fax_attach_file');
    const file = fileInput && fileInput.files && fileInput.files[0];
    if (file) {{
      const name = (file.name || '').toLowerCase();
      if (!(name.endsWith('.txt') || name.endsWith('.pdf'))) {{
        alert('Invalid fax attachment.\\n\\nUploaded file must be .txt or .pdf.');
        showTab('fax');
        return false;
      }}
      if (file.size > 100 * 1024 * 1024) {{
        alert('Invalid fax attachment.\\n\\nFile must be smaller than 100 MB.');
        showTab('fax');
        return false;
      }}
    }}
  }}
  const faxStations = countFaxStationsInForm();
  const nums = filledFaxNumbers();
  const users = filledFaxUsers();
  if (nums !== faxStations || users !== faxStations) {{
    alert(
      'Fax configuration mismatch.\\n\\n' +
      'Stations tab has ' + faxStations + ' Fax station(s), but Fax tab has ' +
      nums + ' fax number(s) and ' + users + ' fax user(s).\\n\\n' +
      'These counts must match before you can Save all.'
    );
    showTab('fax');
    return false;
  }}
  return true;
}}
(function () {{
  const fromHash = (location.hash || '').replace('#', '');
  const start = ['stations','portal','fax','sharepoint','lab','guide'].includes(fromHash)
    ? fromHash
    : document.getElementById('active_tab').value || 'stations';
  showTab(start);
  toggleAttachUpload();
}})();
</script>
</body>
</html>"""
    return body.encode("utf-8")


def parse_stations(form) -> list[dict[str, str]]:
    idxs: set[int] = set()
    for key in form_keys(form):
        if key.startswith("st_") and key.endswith("_name"):
            mid = key[len("st_") : -len("_name")]
            if mid.isdigit():
                idxs.add(int(mid))
    stations: list[dict[str, str]] = []
    for i in sorted(idxs):
        if fget(form, f"st_{i}_delete") == "1":
            continue
        name = fget(form, f"st_{i}_name")
        imei = fget(form, f"st_{i}_imei")
        if not name and not imei:
            continue
        stype = fget(form, f"st_{i}_station_type") or station_type_of(
            {"name": name, "status_header": fget(form, f"st_{i}_status_header"), "access": fget(form, f"st_{i}_access")}
        )
        status = fget(form, f"st_{i}_status_header")
        if not status:
            if stype == "fax":
                status = "Fax Status"
            elif stype == "voicelink":
                status = "Voicelink Status"
            else:
                status = ""
        fax_user = fget(form, f"st_{i}_fax_user") if stype == "fax" else ""
        stations.append(
            {
                "index": str(len(stations)),
                "name": name,
                "imei": imei,
                "anydesk": fget(form, f"st_{i}_anydesk"),
                "ssid": fget(form, f"st_{i}_ssid"),
                "password": fget(form, f"st_{i}_password"),
                "access": fget(form, f"st_{i}_access", "wifi") or "wifi",
                "ssh_host": "",
                "sheet": fget(form, f"st_{i}_sheet") or name,
                "sheet_alt": "",
                "status_header": status,
                "fax_user": fax_user,
            }
        )
    return stations


def parse_fax_numbers(form) -> list[str]:
    idxs = []
    for key in form_keys(form):
        if key.startswith("fax_number_"):
            suf = key[len("fax_number_") :]
            if suf.isdigit():
                idxs.append(int(suf))
    out = []
    for i in sorted(set(idxs)):
        val = fget(form, f"fax_number_{i}")
        if val:
            out.append(val)
    return out


def parse_fax_users(form) -> list[str]:
    idxs = []
    for key in form_keys(form):
        if key.startswith("fax_user_name_"):
            suf = key[len("fax_user_name_") :]
            if suf.isdigit():
                idxs.append(int(suf))
    out = []
    for i in sorted(set(idxs)):
        user = fget(form, f"fax_user_name_{i}")
        imei = fget(form, f"fax_user_imei_{i}")
        if user or imei:
            out.append(f"{user}:{imei}")
    return out


def resolve_fax_attach(form) -> tuple[str, str | None]:
    """Return (attach_path, error)."""
    mode = fget(form, "fax_attach_mode", "default") or "default"
    current = fget(form, "fax_attach_current", DEFAULT_FAX_ATTACH) or DEFAULT_FAX_ATTACH
    if mode == "default":
        return DEFAULT_FAX_ATTACH, None

    # upload mode — new file optional; otherwise keep current
    item = None
    if not isinstance(form, dict) and "fax_attach_file" in form:
        item = form["fax_attach_file"]
        if isinstance(item, list):
            item = item[0]
    if item is None or not getattr(item, "filename", None) or not str(item.filename).strip():
        return current, None

    filename = safe_attach_name(str(item.filename))
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_FAX_EXT:
        return "", "Invalid fax attachment: file must be .txt or .pdf."

    # Read file and check size
    data = item.file.read()
    if len(data) > MAX_FAX_ATTACH_BYTES:
        return "", "Invalid fax attachment: file must be smaller than 100 MB."
    if len(data) == 0:
        return "", "Invalid fax attachment: uploaded file is empty."

    os.makedirs(FAX_UPLOAD_DIR, exist_ok=True)
    dest = os.path.join(FAX_UPLOAD_DIR, filename)
    # Avoid clobbering unrelated files with same name by uniquifying if needed
    if os.path.isfile(dest):
        stem, ext2 = os.path.splitext(filename)
        dest = os.path.join(FAX_UPLOAD_DIR, f"{stem}_{os.getpid()}{ext2}")
    with open(dest, "wb") as out:
        out.write(data)
    # Store relative path with forward slashes for conf portability
    rel = dest.replace("\\", "/")
    return rel, None


def count_fax_stations(stations: list[dict[str, str]]) -> int:
    n = 0
    for st in stations:
        name = (st.get("name") or "").lower()
        status = st.get("status_header") or ""
        if "fax" in name or status == "Fax Status":
            n += 1
    return n


def validate_fax_counts(stations: list[dict[str, str]], numbers: list[str], users: list[str]) -> str | None:
    fax_n = count_fax_stations(stations)
    if len(numbers) != fax_n or len(users) != fax_n:
        return (
            f"Fax configuration mismatch: Stations tab has {fax_n} Fax station(s), "
            f"but Fax tab has {len(numbers)} fax number(s) and {len(users)} fax user(s). "
            "These counts must match before saving."
        )
    return None


def clear_sharepoint_token_cache_if_creds_changed(
    sp_conf: str, new_username: str, new_password: str
) -> str | None:
    """
    If SharePoint username or password changed, delete the MSAL token cache
    so the next sharepoint_excel run signs in with the new account.
    Returns a short status note, or None if credentials were unchanged.
    """
    old = load_conf(sp_conf)
    old_user = (old.get("username") or old.get("email") or "").strip()
    old_pass = old.get("password") or ""
    if new_username.strip() == old_user and new_password == old_pass:
        return None
    cache_rel = (old.get("token_cache") or "token_cache.bin").strip() or "token_cache.bin"
    candidates = []
    if os.path.isabs(cache_rel):
        candidates.append(cache_rel)
    else:
        candidates.append(os.path.abspath(cache_rel))
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(sp_conf)), cache_rel))
    removed = False
    seen: set[str] = set()
    for path in candidates:
        path = os.path.abspath(path)
        if path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed = True
            except OSError:
                pass
    if removed:
        return " Cleared SharePoint token_cache.bin (credentials changed)."
    return " SharePoint credentials changed (no token_cache.bin found to clear)."


def save_form(form) -> tuple[str, str | None]:
    """Returns (message, error). error set => nothing written."""
    def grab(prefix: str, keys: tuple[str, ...]) -> dict[str, str]:
        return {k: fget(form, f"{prefix}_{k}") for k in keys}

    stations = parse_stations(form)
    numbers = parse_fax_numbers(form)
    users = parse_fax_users(form)
    err = validate_fax_counts(stations, numbers, users)
    if err:
        return "", err

    attach_path, attach_err = resolve_fax_attach(form)
    if attach_err:
        return "", attach_err

    sp_updates = grab("sharepoint", SHAREPOINT_KEYS)
    cache_note = clear_sharepoint_token_cache_if_creds_changed(
        STATE["sharepoint_conf"],
        sp_updates.get("username") or "",
        sp_updates.get("password") or "",
    )

    write_conf_updates(STATE["portal_conf"], grab("portal", PORTAL_KEYS))
    fax_updates = grab("fax", FAX_BASIC_KEYS)
    fax_updates["fax_numbers"] = ",".join(numbers)
    fax_updates["fax_users"] = ",".join(users)
    fax_updates["attach"] = attach_path
    write_conf_updates(STATE["fax_conf"], fax_updates)
    write_conf_updates(STATE["sharepoint_conf"], sp_updates)
    write_conf_updates(STATE["monitor_conf"], grab("monitor", MONITOR_KEYS))

    for i, st in enumerate(stations):
        st["index"] = str(i)
    replace_router_block(STATE["monitor_conf"], stations)
    write_portal_imeis_csv(STATE["imei_csv"], stations)
    if not users:
        derived = fax_users_from_stations(stations)
        if derived:
            write_conf_updates(STATE["fax_conf"], {"fax_users": derived})
            users = split_csv(derived)

    return (
        f"Saved {len(stations)} station(s), {len(numbers)} fax number(s), "
        f"{len(users)} fax user(s), attach={attach_path}.{cache_note or ''}",
        None,
    )


def parse_multipart(handler: BaseHTTPRequestHandler) -> FormData:
    """Parse multipart/form-data using email (cgi was removed in Python 3.13)."""
    ctype = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length") or "0")
    body = handler.rfile.read(length)
    msg = message_from_bytes(
        b"Content-Type: " + ctype.encode("utf-8", errors="replace") + b"\r\n\r\n" + body,
        policy=HTTP,
    )
    form = FormData()
    if not msg.is_multipart():
        return form
    for part in msg.iter_parts():
        disp = part.get("Content-Disposition", "")
        if "form-data" not in disp:
            continue
        name = part.get_param("name", header="Content-Disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = b""
        elif isinstance(payload, str):
            payload = payload.encode("utf-8", errors="replace")
        if filename is not None:
            form.add_file(name, UploadedFile(filename, payload))
        else:
            charset = part.get_content_charset() or "utf-8"
            form.add_value(name, payload.decode(charset, errors="replace"))
    return form


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:
        data = page()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "multipart/form-data" in ctype:
            form = parse_multipart(self)
        else:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            form = parse_qs(raw, keep_blank_values=True)
        active = fget(form, "active_tab", "stations") or "stations"
        msg = ""
        err = ""
        if fget(form, "restore_defaults") or fget(form, "reload"):
            msg = "Restored values from saved config files on disk (form edits discarded)."
        else:
            try:
                msg, err_s = save_form(form)
                if err_s:
                    err = err_s
                    active = "fax"
            except Exception as exc:
                err = f"Save failed: {type(exc).__name__}: {exc}"
        data = page(msg, active_tab=active, error=err)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--monitor-conf", default="monitor.conf")
    parser.add_argument("--portal-conf", default="portal.conf")
    parser.add_argument("--fax-conf", default="fax.conf")
    parser.add_argument("--sharepoint-conf", default="sharepoint.conf")
    parser.add_argument("--imei-csv", default="scripts/portal_imeis.csv")
    args = parser.parse_args()

    STATE.update(
        {
            "monitor_conf": args.monitor_conf,
            "portal_conf": args.portal_conf,
            "fax_conf": args.fax_conf,
            "sharepoint_conf": args.sharepoint_conf,
            "imei_csv": args.imei_csv,
        }
    )

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    host = "127.0.0.1" if args.bind in ("127.0.0.1", "localhost") else args.bind
    print(f"Config UI: http://{host}:{args.port}/  (Ctrl+C to stop)", flush=True)
    print("Only start this when you need to change routers / accounts.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
