#!/usr/bin/env python3
"""
On-demand local web UI to edit daily_monitor configs.

  .venv/bin/python3 scripts/config_web.py
  # open http://<pi-ip>:8765
  # Ctrl+C when done — do NOT run as a 24/7 service

Saves the same files as config_wizard.py. No make needed for conf changes.
Binds to 0.0.0.0 by default so lab laptops can open it; use --bind 127.0.0.1
if you only want localhost.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from stations_lib import (
    FAX_KEYS,
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


def esc(v: str) -> str:
    return html.escape(v or "", quote=True)


def input_row(name: str, value: str, secret: bool = False) -> str:
    typ = "password" if secret else "text"
    return (
        f'<label>{esc(name)}'
        f'<input type="{typ}" name="{esc(name)}" value="{esc(value)}" autocomplete="off"></label>'
    )


def station_card(i: int, st: dict[str, str]) -> str:
    fields = [
        ("name", False),
        ("imei", False),
        ("anydesk", False),
        ("ssid", False),
        ("password", True),
        ("access", False),
        ("sheet", False),
        ("sheet_alt", False),
        ("status_header", False),
        ("fax_user", False),
    ]
    rows = []
    for key, secret in fields:
        val = st.get(key) or ""
        if key == "sheet" and not val:
            val = st.get("name") or ""
        if key == "status_header" and val is None:
            val = default_status_header(st.get("name") or "")
        rows.append(input_row(f"st_{i}_{key}", val, secret=secret))
    return (
        f'<fieldset class="card"><legend>Station {i}</legend>'
        + "".join(rows)
        + f'<label class="check"><input type="checkbox" name="st_{i}_delete"> Delete this station</label>'
        + "</fieldset>"
    )


def page(message: str = "") -> bytes:
    monitor = STATE["monitor_conf"]
    portal = STATE["portal_conf"]
    fax = STATE["fax_conf"]
    share = STATE["sharepoint_conf"]
    stations = load_stations(monitor)
    portal_cfg = load_conf(portal)
    fax_cfg = load_conf(fax)
    sp_cfg = load_conf(share)
    mon_cfg = load_conf(monitor)

    station_html = "".join(station_card(i, st) for i, st in enumerate(stations))
    if not stations:
        station_html = "<p>No stations yet — use Add empty station below.</p>"

    def section(title: str, prefix: str, keys: tuple[str, ...], cfg: dict[str, str]) -> str:
        rows = []
        for key in keys:
            secret = "password" in key or "secret" in key or "totp" in key or key.endswith("_key")
            rows.append(input_row(f"{prefix}_{key}", cfg.get(key) or "", secret=secret))
        return f'<fieldset class="card"><legend>{esc(title)}</legend>{"".join(rows)}</fieldset>'

    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Simplifi monitor config</title>
<style>
body{{font-family:Segoe UI,system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;background:#f6f7f9;color:#1b1f24}}
h1{{font-size:1.4rem;margin:0 0 8px}}
.note{{color:#555;margin-bottom:16px}}
.card{{background:#fff;border:1px solid #d8dde6;border-radius:8px;padding:12px 14px;margin:12px 0}}
label{{display:block;margin:8px 0;font-size:.9rem}}
input[type=text],input[type=password]{{display:block;width:100%;box-sizing:border-box;margin-top:4px;padding:8px;border:1px solid #c5ccd8;border-radius:6px}}
.check{{display:flex;align-items:center;gap:8px;margin-top:10px}}
.check input{{width:auto}}
button{{background:#1f6feb;color:#fff;border:0;border-radius:6px;padding:10px 16px;font-size:1rem;cursor:pointer;margin-right:8px}}
button.secondary{{background:#5c6570}}
.ok{{background:#e7f6ed;border:1px solid #b7e0c2;padding:10px;border-radius:6px}}
.actions{{position:sticky;bottom:0;background:#f6f7f9;padding:12px 0;margin-top:8px}}
</style></head><body>
<h1>Simplifi daily_monitor config</h1>
<p class="note">On-demand only — stop this server (Ctrl+C) when finished. Saving writes conf files; no <code>make</code> needed.</p>
{msg}
<form method="POST" action="/save">
{section("Portal", "portal", PORTAL_KEYS, portal_cfg)}
{section("Fax", "fax", FAX_KEYS, fax_cfg)}
{section("SharePoint", "sharepoint", SHAREPOINT_KEYS, sp_cfg)}
{section("Lab / Jump / Cursor", "monitor", ("lab_ssid_5g","lab_ssid_24g","lab_password","jump_host","jump_user","cursor_api_key"), mon_cfg)}
<h2>Stations</h2>
{station_html}
<label class="check"><input type="checkbox" name="add_station"> Add empty station</label>
<div class="actions">
<button type="submit">Save all</button>
<button type="submit" name="reload" value="1" class="secondary" formnovalidate>Reload from disk</button>
</div>
</form>
</body></html>"""
    return body.encode("utf-8")


def parse_stations(form: dict[str, list[str]]) -> list[dict[str, str]]:
    # Discover indices from st_N_name keys
    idxs: set[int] = set()
    for key in form:
        if key.startswith("st_") and key.endswith("_name"):
            mid = key[len("st_") : -len("_name")]
            if mid.isdigit():
                idxs.add(int(mid))
    stations: list[dict[str, str]] = []
    for i in sorted(idxs):
        if form.get(f"st_{i}_delete", [""])[0]:
            continue
        name = (form.get(f"st_{i}_name") or [""])[0].strip()
        imei = (form.get(f"st_{i}_imei") or [""])[0].strip()
        if not name and not imei:
            continue
        stations.append(
            {
                "index": str(len(stations)),
                "name": name,
                "imei": imei,
                "anydesk": (form.get(f"st_{i}_anydesk") or [""])[0].strip(),
                "ssid": (form.get(f"st_{i}_ssid") or [""])[0].strip(),
                "password": (form.get(f"st_{i}_password") or [""])[0].strip(),
                "access": (form.get(f"st_{i}_access") or ["wifi"])[0].strip() or "wifi",
                "ssh_host": "",
                "sheet": (form.get(f"st_{i}_sheet") or [name])[0].strip() or name,
                "sheet_alt": (form.get(f"st_{i}_sheet_alt") or [""])[0].strip(),
                "status_header": (form.get(f"st_{i}_status_header") or [""])[0],
                "fax_user": (form.get(f"st_{i}_fax_user") or [""])[0].strip(),
            }
        )
    if form.get("add_station", [""])[0]:
        stations.append(
            {
                "index": str(len(stations)),
                "name": "",
                "imei": "",
                "anydesk": "",
                "ssid": "",
                "password": "",
                "access": "wifi",
                "ssh_host": "",
                "sheet": "",
                "sheet_alt": "",
                "status_header": "",
                "fax_user": "",
            }
        )
    return stations


def save_form(form: dict[str, list[str]]) -> str:
    def grab(prefix: str, keys: tuple[str, ...]) -> dict[str, str]:
        return {k: (form.get(f"{prefix}_{k}") or [""])[0] for k in keys}

    write_conf_updates(STATE["portal_conf"], grab("portal", PORTAL_KEYS))
    write_conf_updates(STATE["fax_conf"], grab("fax", FAX_KEYS))
    write_conf_updates(STATE["sharepoint_conf"], grab("sharepoint", SHAREPOINT_KEYS))
    write_conf_updates(
        STATE["monitor_conf"],
        grab("monitor", ("lab_ssid_5g", "lab_ssid_24g", "lab_password", "jump_host", "jump_user", "cursor_api_key")),
    )
    stations = parse_stations(form)
    # Drop the blank placeholder if user only ticked add without filling
    stations = [s for s in stations if (s.get("name") or s.get("imei"))]
    for i, st in enumerate(stations):
        st["index"] = str(i)
    replace_router_block(STATE["monitor_conf"], stations)
    write_portal_imeis_csv(STATE["imei_csv"], stations)
    derived = fax_users_from_stations(stations)
    if derived:
        write_conf_updates(STATE["fax_conf"], {"fax_users": derived})
    return (
        f"Saved {len(stations)} station(s) to {STATE['monitor_conf']}, "
        f"synced {STATE['imei_csv']}"
        + (f", fax_users={derived}" if derived else "")
    )


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
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        form = parse_qs(raw, keep_blank_values=True)
        if form.get("reload", [""])[0]:
            msg = "Reloaded from disk (not saved)."
        else:
            try:
                msg = save_form(form)
            except Exception as exc:
                msg = f"Save failed: {type(exc).__name__}: {exc}"
        data = page(msg)
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
