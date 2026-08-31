#!/usr/bin/env python3
"""
Laptop test: does #QUEUES_REFRESH actually reload ReceivedPendingDeletion?

Does NOT send a fax. Every 5s: click Refresh, print row count + last User,
stop when either changes (delete the last row, or any rows, on cloud.faxback.net
in your other browser).

From the repo root (headed Chrome):

  python scripts/test_fax_refresh.py
  python scripts/test_fax_refresh.py --conf fax.conf --interval 5
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.chdir(ROOT)

from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

import send_fax as fax  # noqa: E402

GRID_INFO_JS = """
function pickUser(row) {
  if (!row || typeof row !== "object") return "";
  const keys = ["User", "user", "Username", "username", "USER", "Owner", "Account"];
  for (const k of keys) {
    const v = row[k];
    if (v != null && String(v).trim()) return String(v).trim().split(/\\s+/)[0];
  }
  const skip = /success|pending|received|deletion|^\\d+$|^\\d{10,}$|aug|jan|feb|mar|apr|may|jun|jul|sep|oct|nov|dec/i;
  for (const k of Object.keys(row)) {
    if (!k || k[0] === "_" || k === "uid" || k === "uniqueid" || k === "boundindex") continue;
    const v = row[k];
    if (v == null || typeof v === "object") continue;
    const s = String(v).trim();
    if (s.length >= 3 && s.length <= 40 && !skip.test(s)) return s.split(/\\s+/)[0];
  }
  return "";
}

function dump(row) {
  if (!row || typeof row !== "object") return String(row || "");
  const parts = [];
  for (const k of Object.keys(row)) {
    if (!k || k[0] === "_" || k === "uid") continue;
    const v = row[k];
    if (v == null || typeof v === "object") continue;
    const s = String(v).trim();
    if (s) parts.push(s);
  }
  return parts.join(" | ");
}

const $ = window.jQuery;
let n = 0;
let lastUser = "";
let lastRow = "";
let via = "none";
if ($ && $.fn && $.fn.jqxGrid) {
  $(".jqx-grid").each(function () {
    const grid = $(this);
    let rows = [];
    let infoN = 0;
    try {
      const info = grid.jqxGrid("getdatainformation") || {};
      infoN = info.rowscount || 0;
    } catch (e) {}
    try { rows = grid.jqxGrid("getboundrows") || []; } catch (e) {}
    if (!rows.length) {
      try { rows = grid.jqxGrid("getrows") || []; } catch (e) {}
    }
    const count = Math.max(infoN, rows.length);
    if (count >= n) {
      n = count;
      via = "jqx";
      if (rows.length) {
        const last = rows[rows.length - 1];
        lastUser = pickUser(last);
        lastRow = dump(last).slice(0, 180);
      }
    }
  });
}
return {n: n, lastUser: lastUser, lastRow: lastRow, via: via};
"""


def snapshot(driver) -> dict:
    best = {"n": 0, "lastUser": "", "lastRow": "", "via": "none"}
    for _ in fax.iter_frames(driver):
        try:
            got = driver.execute_script(GRID_INFO_JS) or {}
        except Exception:
            got = {}
        if not isinstance(got, dict):
            continue
        if int(got.get("n") or 0) >= int(best.get("n") or 0) and (
            got.get("lastUser") or got.get("n")
        ):
            best = got
    driver.switch_to.default_content()
    return {
        "n": int(best.get("n") or 0),
        "lastUser": (best.get("lastUser") or "").strip(),
        "lastRow": (best.get("lastRow") or "").strip(),
        "via": best.get("via") or "none",
    }


def poll_once(driver, queue_name: str) -> dict:
    if not fax.queue_filter_selected(driver, queue_name):
        fax.ensure_pending_deletion_queue(driver, WebDriverWait(driver, 15), queue_name)
    refreshed = fax.click_refresh(driver)
    time.sleep(2)
    fax.scroll_queue_grid(driver)
    time.sleep(0.4)
    info = snapshot(driver)
    info["refreshed"] = refreshed
    return info


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", default="fax.conf")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    cfg = fax.load_conf(args.conf)
    user = cfg.get("username") or cfg.get("email") or ""
    password = cfg.get("password") or ""
    url = cfg.get("url") or fax.FAX_URL
    queue_name = cfg.get("queue_name") or "ReceivedPendingDeletion"
    login_wait = int(cfg.get("login_wait_sec") or "15")
    headed = not args.headless

    if not user or not password:
        fax.log("ERROR", f"Fill username= and password= in {args.conf}")
        return 2

    fax.log("INFO", "No fax will be sent — Refresh watch only")
    fax.log("INFO", f"Opening {url}  headed={headed}")
    fax.log("INFO", f"Delete the LAST row (or any rows) on cloud, then wait for next Refresh")
    driver = fax.build_driver(headed)
    wait = WebDriverWait(driver, 30)
    try:
        driver.get(url)
        wait.until(EC.presence_of_element_located((By.ID, "LOGIN_USERNAME")))
        user_el = driver.find_element(By.ID, "LOGIN_USERNAME")
        if not (user_el.get_attribute("value") or "").strip():
            user_el.send_keys(user)
        pass_el = driver.find_element(By.ID, "LOGIN_PASSWORD")
        pass_el.clear()
        pass_el.send_keys(password)
        driver.find_element(By.ID, "LOGIN_OK").click()
        fax.log("INFO", f"Credentials submitted — waiting {login_wait}s")
        time.sleep(login_wait)
        fax.accept_alerts(driver)
        wait.until(EC.presence_of_element_located((By.ID, "APPNAV")))

        if not fax.open_received_pending_queue(driver, wait, queue_name):
            fax.log("WARN", f"Could not confirm queue dropdown is {queue_name}")

        baseline = poll_once(driver, queue_name)
        fax.log(
            "INFO",
            f"Baseline rows={baseline['n']} lastUser={baseline['lastUser'] or '-'} "
            f"refresh={baseline['refreshed']} via={baseline['via']}",
        )
        if baseline["lastRow"]:
            fax.log("INFO", f"Last row: {baseline['lastRow']}")
        if not baseline["refreshed"]:
            fax.log("ERROR", "Refresh button was not clicked — cannot test")
            return 1
        if baseline["n"] == 0 and not baseline["lastUser"]:
            fax.log("WARN", "Grid looks empty; still watching for a change")

        round_i = 0
        while True:
            time.sleep(max(args.interval, 1))
            round_i += 1
            now = poll_once(driver, queue_name)
            fax.log(
                "INFO",
                f"#{round_i} rows={now['n']} lastUser={now['lastUser'] or '-'} "
                f"refresh={now['refreshed']}",
            )
            count_changed = now["n"] != baseline["n"]
            user_changed = (now["lastUser"] or "") != (baseline["lastUser"] or "")
            if count_changed or user_changed:
                why = []
                if count_changed:
                    why.append(f"rows {baseline['n']} -> {now['n']}")
                if user_changed:
                    why.append(
                        f"lastUser {baseline['lastUser'] or '-'} -> {now['lastUser'] or '-'}"
                    )
                fax.log("PASSED", "Refresh saw a change: " + "; ".join(why))
                if now["lastRow"]:
                    fax.log("INFO", f"New last row: {now['lastRow']}")
                return 0
    except KeyboardInterrupt:
        fax.log("INFO", "Stopped by Ctrl+C (no change seen)")
        return 130
    except Exception as exc:
        fax.log("ERROR", f"{type(exc).__name__}: {exc}")
        try:
            fax.dump_shot(driver, "refresh_watch_fail")
        except Exception:
            pass
        return 1
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        shutil.rmtree(fax.CHROME_PROFILE, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
