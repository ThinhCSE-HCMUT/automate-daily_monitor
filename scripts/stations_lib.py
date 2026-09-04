#!/usr/bin/env python3
"""
Shared station config helpers.

Canonical source: monitor.conf router.N.* fields (same file C already loads).
Optional per-station fields used by Python only:
  router.N.sheet          SharePoint sheet primary name (default = name)
  router.N.sheet_alt      alternate sheet name
  router.N.status_header  "Voicelink Status" | "Fax Status" | "" for Virtual
  router.N.fax_user       Faxback queue user (e.g. simplifivn1) — empty if N/A

portal_logs / sharepoint_excel / config_wizard / config_web all use this module
so changing routers no longer requires editing portal_imeis.csv or SHEETS in source.
"""
from __future__ import annotations

import os
import re
from typing import Any


ROUTER_FIELDS = (
    "name",
    "imei",
    "anydesk",
    "ssid",
    "password",
    "access",
    "ssh_host",
    "sheet",
    "sheet_alt",
    "status_header",
    "fax_user",
)

# Flat conf keys the wizard/web may edit (not full conf — skips rarely-changed knobs).
PORTAL_KEYS = ("email", "password", "totp_secret", "portal_url")
FAX_KEYS = ("username", "password", "url", "fax_numbers", "fax_users", "attach")
SHAREPOINT_KEYS = ("username", "password", "tenant", "site_url", "file_url", "file_name", "token_cache")


def load_conf(path: str) -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not path or not os.path.isfile(path):
        return cfg
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            cfg[key.strip()] = val.strip()
    return cfg


def routers_from_conf(cfg: dict[str, str]) -> list[dict[str, str]]:
    idxs: set[int] = set()
    for key in cfg:
        m = re.match(r"^router\.(\d+)\.", key)
        if m:
            idxs.add(int(m.group(1)))
    out: list[dict[str, str]] = []
    for i in sorted(idxs):
        name = cfg.get(f"router.{i}.name") or ""
        imei = cfg.get(f"router.{i}.imei") or ""
        if not name and not imei:
            continue
        sheet = cfg.get(f"router.{i}.sheet") or name
        sheet_alt = cfg.get(f"router.{i}.sheet_alt") or ""
        status = cfg.get(f"router.{i}.status_header")
        if status is None:
            # Infer from name when field omitted (backward compatible).
            low = name.lower()
            if "voicelink" in low:
                status = "Voicelink Status"
            elif "fax" in low:
                status = "Fax Status"
            else:
                status = ""
        access = (cfg.get(f"router.{i}.access") or "wifi").strip().lower() or "wifi"
        out.append(
            {
                "index": str(i),
                "name": name,
                "imei": imei,
                "anydesk": cfg.get(f"router.{i}.anydesk") or "",
                "ssid": cfg.get(f"router.{i}.ssid") or "",
                "password": cfg.get(f"router.{i}.password") or "",
                "access": access,
                "ssh_host": cfg.get(f"router.{i}.ssh_host") or "",
                "sheet": sheet,
                "sheet_alt": sheet_alt,
                "status_header": status,
                "fax_user": cfg.get(f"router.{i}.fax_user") or "",
            }
        )
    return out


def load_stations(monitor_conf: str = "monitor.conf") -> list[dict[str, str]]:
    return routers_from_conf(load_conf(monitor_conf))


def portal_imei_rows(stations: list[dict[str, str]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for st in stations:
        imei = (st.get("imei") or "").strip()
        name = (st.get("name") or imei).strip()
        if imei:
            rows.append((name, imei))
    return rows


def sheets_specs(stations: list[dict[str, str]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for st in stations:
        imei = (st.get("imei") or "").strip()
        if not imei:
            continue
        names: list[str] = []
        for n in (st.get("sheet") or st.get("name") or "", st.get("sheet_alt") or ""):
            n = n.strip()
            if n and n not in names:
                names.append(n)
        if not names:
            continue
        specs.append(
            {
                "imei": imei,
                "names": tuple(names),
                "status_header": st.get("status_header") or "",
            }
        )
    return specs


def fax_users_from_stations(stations: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for st in stations:
        user = (st.get("fax_user") or "").strip()
        imei = (st.get("imei") or "").strip()
        if user and imei:
            parts.append(f"{user}:{imei}")
    return ",".join(parts)


def set_conf_value(lines: list[str], key: str, value: str) -> list[str]:
    """Update key=value in-place, or append if missing. Preserves comments/order."""
    out: list[str] = []
    found = False
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for line in lines:
        if pattern.match(line):
            out.append(f"{key}={value}\n")
            found = True
        else:
            out.append(line if line.endswith("\n") else line + "\n")
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{key}={value}\n")
    return out


def write_conf_updates(path: str, updates: dict[str, str]) -> None:
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = [f"# {os.path.basename(path)}\n"]
    for key, value in updates.items():
        lines = set_conf_value(lines, key, value)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)


def replace_router_block(monitor_path: str, stations: list[dict[str, str]]) -> None:
    """Rewrite all router.N.* keys from stations; keep non-router lines."""
    if os.path.isfile(monitor_path):
        with open(monitor_path, encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = ["# monitor.conf\n"]

    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("router.") and "=" in stripped:
            continue
        kept.append(line if line.endswith("\n") else line + "\n")

    while kept and kept[-1].strip() == "":
        kept.pop()
    kept.append("\n")
    kept.append("# Stations — edited by config_wizard / config_web\n")
    for st in stations:
        i = st.get("index") or "0"
        for field in ROUTER_FIELDS:
            val = st.get(field) or ""
            if field == "access" and not val:
                val = "wifi"
            # Always write core fields; write optional ones when non-empty OR known keys.
            if field in ("ssh_host", "sheet_alt", "fax_user") and not val:
                continue
            if field == "sheet" and (not val or val == st.get("name")):
                # omit when same as name — inferred at load time
                continue
            if field == "status_header" and val == "":
                kept.append(f"router.{i}.status_header=\n")
                continue
            kept.append(f"router.{i}.{field}={val}\n")
        kept.append("\n")

    with open(monitor_path, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(kept)


def write_portal_imeis_csv(path: str, stations: list[dict[str, str]]) -> None:
    """Optional sync for humans/tools that still open the CSV."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("name,data\n")
        for name, imei in portal_imei_rows(stations):
            f.write(f"{name},{imei}\n")


def default_status_header(name: str) -> str:
    low = (name or "").lower()
    if "voicelink" in low:
        return "Voicelink Status"
    if "fax" in low:
        return "Fax Status"
    return ""
