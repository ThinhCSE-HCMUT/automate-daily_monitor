#!/usr/bin/env python3
"""
Interactive CLI to edit daily_monitor config (run only when routers change).

  .venv/bin/python3 scripts/config_wizard.py
  .venv/bin/python3 scripts/config_wizard.py --monitor-conf monitor.conf

Writes:
  monitor.conf          (stations + lab/jump fields users care about)
  portal.conf / fax.conf / sharepoint.conf
  scripts/portal_imeis.csv  (synced copy for humans)
  fax_users= derived from station fax_user fields

No make needed — conf-only changes apply on the next ./monitor run.
"""
from __future__ import annotations

import argparse
import os
import sys

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


def ask(prompt: str, current: str = "", secret: bool = False) -> str:
    hint = "****" if secret and current else (current or "")
    suffix = f" [{hint}]" if hint else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    if raw == "":
        return current
    return raw


def edit_kv(title: str, path: str, keys: tuple[str, ...]) -> None:
    print(f"\n=== {title} ({path}) ===")
    cfg = load_conf(path)
    updates: dict[str, str] = {}
    for key in keys:
        secret = "password" in key or "secret" in key or "totp" in key
        updates[key] = ask(key, cfg.get(key) or "", secret=secret)
    write_conf_updates(path, updates)
    print(f"Saved {path}")


def edit_stations(monitor_conf: str) -> list[dict[str, str]]:
    stations = load_stations(monitor_conf)
    if not stations:
        print("No stations in monitor.conf — creating empty list.")
        stations = []

    while True:
        print("\n=== Stations ===")
        for i, st in enumerate(stations):
            print(
                f"  [{i}] {st.get('name') or '(unnamed)'}  IMEI={st.get('imei') or '-'}  "
                f"access={st.get('access') or 'wifi'}  fax_user={st.get('fax_user') or '-'}"
            )
        print("  [a] add station")
        print("  [d] delete station")
        print("  [Enter] done")
        choice = input("Choice: ").strip().lower()
        if choice == "":
            break
        if choice == "a":
            idx = str(len(stations))
            name = ask("name")
            stations.append(
                {
                    "index": idx,
                    "name": name,
                    "imei": ask("imei"),
                    "anydesk": ask("anydesk"),
                    "ssid": ask("ssid"),
                    "password": ask("password", secret=True),
                    "access": ask("access (wifi|tailscale)", "wifi"),
                    "ssh_host": "",
                    "sheet": name,
                    "sheet_alt": "",
                    "status_header": ask(
                        "status_header (Voicelink Status|Fax Status|empty)",
                        default_status_header(name),
                    ),
                    "fax_user": ask("fax_user (Fax stations only, else empty)", ""),
                }
            )
            continue
        if choice == "d":
            raw = input("Index to delete: ").strip()
            if raw.isdigit() and 0 <= int(raw) < len(stations):
                stations.pop(int(raw))
                for i, st in enumerate(stations):
                    st["index"] = str(i)
            continue
        if choice.isdigit() and 0 <= int(choice) < len(stations):
            st = stations[int(choice)]
            print(f"\n--- Edit station {choice} ---")
            st["name"] = ask("name", st.get("name") or "")
            st["imei"] = ask("imei", st.get("imei") or "")
            st["anydesk"] = ask("anydesk", st.get("anydesk") or "")
            st["ssid"] = ask("ssid", st.get("ssid") or "")
            st["password"] = ask("password", st.get("password") or "", secret=True)
            st["access"] = ask("access (wifi|tailscale)", st.get("access") or "wifi")
            st["sheet"] = ask("sheet (SharePoint sheet name)", st.get("sheet") or st.get("name") or "")
            st["sheet_alt"] = ask("sheet_alt (optional)", st.get("sheet_alt") or "")
            st["status_header"] = ask(
                "status_header",
                st.get("status_header") if st.get("status_header") is not None else default_status_header(st.get("name") or ""),
            )
            st["fax_user"] = ask("fax_user", st.get("fax_user") or "")
            st["index"] = str(choice)
    return stations


def edit_monitor_misc(monitor_conf: str) -> None:
    print(f"\n=== Monitor lab / jump ({monitor_conf}) ===")
    cfg = load_conf(monitor_conf)
    keys = (
        "lab_ssid_5g",
        "lab_ssid_24g",
        "lab_password",
        "jump_host",
        "jump_user",
        "cursor_api_key",
    )
    updates = {}
    for key in keys:
        secret = "password" in key or "key" in key
        updates[key] = ask(key, cfg.get(key) or "", secret=secret)
    write_conf_updates(monitor_conf, updates)


def save_all(
    monitor_conf: str,
    portal_conf: str,
    fax_conf: str,
    sharepoint_conf: str,
    stations: list[dict[str, str]],
    imei_csv: str,
) -> None:
    for i, st in enumerate(stations):
        st["index"] = str(i)
    replace_router_block(monitor_conf, stations)
    write_portal_imeis_csv(imei_csv, stations)
    fax_users = fax_users_from_stations(stations)
    if fax_users:
        write_conf_updates(fax_conf, {"fax_users": fax_users})
    print(f"\nSaved stations → {monitor_conf}")
    print(f"Synced IMEI list → {imei_csv}")
    if fax_users:
        print(f"Synced fax_users → {fax_conf}")
    print("No make needed. Next ./monitor run will use the new config.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Edit daily_monitor configs (infrequent)")
    parser.add_argument("--monitor-conf", default="monitor.conf")
    parser.add_argument("--portal-conf", default="portal.conf")
    parser.add_argument("--fax-conf", default="fax.conf")
    parser.add_argument("--sharepoint-conf", default="sharepoint.conf")
    parser.add_argument("--imei-csv", default="scripts/portal_imeis.csv")
    args = parser.parse_args()

    stations = load_stations(args.monitor_conf)
    while True:
        print(
            "\nSimplifi daily_monitor config wizard\n"
            "  1) Stations (name/IMEI/SSID/sheet/fax_user)\n"
            "  2) Portal login\n"
            "  3) Faxback login / numbers\n"
            "  4) SharePoint login / file\n"
            "  5) Lab WiFi + jump host + Cursor API key\n"
            "  s) Save stations + sync CSV/fax_users\n"
            "  q) Quit"
        )
        choice = input("Choice: ").strip().lower()
        if choice == "1":
            stations = edit_stations(args.monitor_conf)
        elif choice == "2":
            edit_kv("Portal", args.portal_conf, PORTAL_KEYS)
        elif choice == "3":
            edit_kv("Fax", args.fax_conf, FAX_KEYS)
            # Keep station-derived fax_users in sync if present
            derived = fax_users_from_stations(stations)
            if derived:
                write_conf_updates(args.fax_conf, {"fax_users": derived})
                print(f"Note: fax_users overwritten from stations → {derived}")
        elif choice == "4":
            edit_kv("SharePoint", args.sharepoint_conf, SHAREPOINT_KEYS)
        elif choice == "5":
            edit_monitor_misc(args.monitor_conf)
        elif choice == "s":
            save_all(
                args.monitor_conf,
                args.portal_conf,
                args.fax_conf,
                args.sharepoint_conf,
                stations,
                args.imei_csv,
            )
        elif choice == "q":
            print("Bye.")
            return 0
        else:
            print("Unknown choice")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
