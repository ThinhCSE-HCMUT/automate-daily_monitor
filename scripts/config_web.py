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

MONITOR_KEYS = (
    "lab_ssid_5g",
    "lab_ssid_24g",
    "lab_password",
    "jump_host",
    "jump_user",
    "cursor_api_key",
)

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
    "fax_numbers": "Fax numbers",
    "fax_users": "Fax users (user:imei,...)",
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


def friendly_label(key: str) -> str:
    return FIELD_LABELS.get(key, key.replace("_", " ").title())


def is_secret(key: str) -> bool:
    k = key.lower()
    return "password" in k or "secret" in k or "totp" in k or k.endswith("_key")


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
        rows.append(
            input_row(f"st_{i}_{key}", val, secret=secret, label=friendly_label(key))
        )
    title = esc(st.get("name") or f"Station {i}")
    return f"""
    <article class="station-card">
      <header class="station-head">
        <h3>{title}</h3>
        <span class="badge">#{i}</span>
      </header>
      <div class="grid">{"".join(rows)}</div>
      <label class="check danger">
        <input type="checkbox" name="st_{i}_delete"> Delete this station
      </label>
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


def page(message: str = "", active_tab: str = "stations") -> bytes:
    monitor = STATE["monitor_conf"]
    portal = STATE["portal_conf"]
    fax = STATE["fax_conf"]
    share = STATE["sharepoint_conf"]
    stations = load_stations(monitor)
    portal_cfg = load_conf(portal)
    fax_cfg = load_conf(fax)
    sp_cfg = load_conf(share)
    mon_cfg = load_conf(monitor)

    tab = active_tab if active_tab in ("stations", "portal", "fax", "sharepoint", "lab") else "stations"
    station_html = "".join(station_card(i, st) for i, st in enumerate(stations))
    if not stations:
        station_html = '<p class="empty">No stations yet — tick “Add empty station” then Save.</p>'

    msg = f'<div class="toast ok" role="status">{esc(message)}</div>' if message else ""

    def tab_btn(tid: str, label: str) -> str:
        on = " active" if tab == tid else ""
        return f'<button type="button" class="tab{on}" data-tab="{tid}" onclick="showTab(\'{tid}\')">{label}</button>'

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
.wrap {{
  width: min(980px, calc(100% - 28px));
  margin: 28px auto 48px;
}}
.hero {{
  background: linear-gradient(135deg, #143a6b, #1a6fd4 55%, #3d93e8);
  color: #fff;
  border-radius: calc(var(--radius) + 4px);
  padding: 22px 24px;
  box-shadow: var(--shadow);
}}
.hero h1 {{ margin: 0 0 6px; font-size: clamp(1.25rem, 3vw, 1.7rem); }}
.hero p {{ margin: 0; opacity: .9; font-size: .95rem; line-height: 1.45; }}
.tabs {{
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 18px 0 14px;
}}
.tab {{
  border: 1px solid var(--line);
  background: rgba(255,255,255,.72);
  color: var(--ink);
  border-radius: 999px;
  padding: 10px 16px;
  font-weight: 600;
  cursor: pointer;
  transition: transform .15s ease, background .15s ease, border-color .15s ease, box-shadow .15s ease;
}}
.tab:hover {{
  transform: translateY(-1px);
  border-color: #9db8de;
  box-shadow: 0 6px 16px rgba(26,111,212,.12);
}}
.tab.active {{
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}}
.panel {{ display: none; animation: fade .18s ease; }}
.panel.active {{ display: block; }}
@keyframes fade {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: none; }} }}
.card, .station-card {{
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 16px;
  margin: 0 0 14px;
  box-shadow: var(--shadow);
  transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
}}
.station-card:hover, .card:hover {{
  transform: translateY(-2px);
  border-color: #b7c9e2;
  box-shadow: 0 14px 34px rgba(21,32,43,.12);
}}
.station-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}}
.station-head h3 {{ margin: 0; font-size: 1.05rem; }}
.badge {{
  background: #eaf2ff;
  color: var(--accent-2);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: .8rem;
  font-weight: 700;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 14px;
}}
.field {{ display: block; margin: 0; }}
.field-label {{
  display: block;
  margin-bottom: 6px;
  color: var(--muted);
  font-size: .82rem;
  font-weight: 600;
}}
input[type=text], input[type=password] {{
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 11px 12px;
  font-size: 0.98rem;
  background: #fbfcfe;
  transition: border-color .15s ease, box-shadow .15s ease, background .15s ease;
}}
input[type=text]:hover, input[type=password]:hover {{
  border-color: #a9bdd9;
  background: #fff;
}}
input[type=text]:focus, input[type=password]:focus {{
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(26,111,212,.18);
  background: #fff;
}}
.pw-wrap {{ position: relative; }}
.pw-wrap input {{ padding-right: 46px; }}
.eye-btn {{
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  border: 0;
  background: transparent;
  color: var(--muted);
  width: 36px;
  height: 36px;
  border-radius: 8px;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: background .15s ease, color .15s ease;
}}
.eye-btn:hover {{ background: #eaf2ff; color: var(--accent-2); }}
.eye-btn .icon-eye-off {{ display: none; }}
.eye-btn.is-visible .icon-eye {{ display: none; }}
.eye-btn.is-visible .icon-eye-off {{ display: block; }}
.check {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: .92rem;
}}
.check.danger {{ color: var(--danger); }}
.check input {{ width: auto; }}
.empty {{ color: var(--muted); }}
.actions {{
  position: sticky;
  bottom: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 14px 0 4px;
  background: linear-gradient(to top, var(--bg) 70%, transparent);
}}
.btn {{
  border: 0;
  border-radius: 10px;
  padding: 12px 18px;
  font-size: 1rem;
  font-weight: 700;
  cursor: pointer;
  transition: transform .15s ease, box-shadow .15s ease, background .15s ease;
}}
.btn:hover {{ transform: translateY(-1px); }}
.btn-primary {{
  background: var(--accent);
  color: #fff;
  box-shadow: 0 8px 18px rgba(26,111,212,.25);
}}
.btn-primary:hover {{ background: var(--accent-2); }}
.btn-secondary {{
  background: #5f6b7a;
  color: #fff;
}}
.btn-secondary:hover {{ background: #4a5563; }}
.toast {{
  margin: 14px 0 0;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--ok-line);
  background: var(--ok-bg);
}}
@media (max-width: 720px) {{
  .wrap {{ width: calc(100% - 20px); margin: 16px auto 36px; }}
  .hero {{ padding: 18px; }}
  .grid {{ grid-template-columns: 1fr; }}
  .tab {{ flex: 1 1 calc(50% - 8px); text-align: center; }}
  .actions {{ flex-direction: column; }}
  .btn {{ width: 100%; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1>Simplifi Daily Monitor Settings</h1>
  </header>
  {msg}
  <form method="POST" action="/save" id="cfg-form">
    <input type="hidden" name="active_tab" id="active_tab" value="{esc(tab)}">
    <nav class="tabs" aria-label="Config sections">
      {tab_btn("stations", "Stations")}
      {tab_btn("portal", "Portal")}
      {tab_btn("fax", "Fax")}
      {tab_btn("sharepoint", "SharePoint")}
      {tab_btn("lab", "Lab / Jump")}
    </nav>

    <section class="panel{' active' if tab == 'stations' else ''}" id="tab-stations">
      <div class="card">
        <p class="empty" style="margin:0 0 10px">Edit routers here. SharePoint sheet mapping and portal IMEI list follow these stations.</p>
        {station_html}
        <label class="check"><input type="checkbox" name="add_station"> Add empty station</label>
      </div>
    </section>

    <section class="panel{' active' if tab == 'portal' else ''}" id="tab-portal">
      <div class="card"><div class="grid">{fields_block("portal", PORTAL_KEYS, portal_cfg)}</div></div>
    </section>

    <section class="panel{' active' if tab == 'fax' else ''}" id="tab-fax">
      <div class="card"><div class="grid">{fields_block("fax", FAX_KEYS, fax_cfg)}</div></div>
    </section>

    <section class="panel{' active' if tab == 'sharepoint' else ''}" id="tab-sharepoint">
      <div class="card"><div class="grid">{fields_block("sharepoint", SHAREPOINT_KEYS, sp_cfg)}</div></div>
    </section>

    <section class="panel{' active' if tab == 'lab' else ''}" id="tab-lab">
      <div class="card"><div class="grid">{fields_block("monitor", MONITOR_KEYS, mon_cfg)}</div></div>
    </section>

    <div class="actions">
      <button type="submit" class="btn btn-primary">Save all</button>
      <button type="submit" name="restore_defaults" value="1" class="btn btn-secondary" formnovalidate>Restore Default Settings</button>
    </div>
  </form>
</div>
<script>
function showTab(id) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const panel = document.getElementById('tab-' + id);
  if (panel) panel.classList.add('active');
  document.querySelectorAll('.tab[data-tab="' + id + '"]').forEach(t => t.classList.add('active'));
  const hidden = document.getElementById('active_tab');
  if (hidden) hidden.value = id;
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
(function () {{
  const fromHash = (location.hash || '').replace('#', '');
  const start = ['stations','portal','fax','sharepoint','lab'].includes(fromHash)
    ? fromHash
    : document.getElementById('active_tab').value || 'stations';
  showTab(start);
}})();
</script>
</body>
</html>"""
    return body.encode("utf-8")


def parse_stations(form: dict[str, list[str]]) -> list[dict[str, str]]:
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
    write_conf_updates(STATE["monitor_conf"], grab("monitor", MONITOR_KEYS))
    stations = parse_stations(form)
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
        active = (form.get("active_tab") or ["stations"])[0]
        if form.get("restore_defaults", [""])[0] or form.get("reload", [""])[0]:
            msg = "Restored values from saved config files on disk (form edits discarded)."
        else:
            try:
                msg = save_form(form)
            except Exception as exc:
                msg = f"Save failed: {type(exc).__name__}: {exc}"
        data = page(msg, active_tab=active)
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
