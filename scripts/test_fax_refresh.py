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
from fax_queue_match import extract_user  # noqa: E402


def snapshot(driver) -> dict:
    rows, meta = fax.collect_queue_rows(driver, "pair")
    last = meta.get("lastRow") or (rows[-1] if rows else "")
    return {
        "n": int(meta.get("n") or 0),
        "lastUser": (meta.get("lastUser") or extract_user(last) or "").strip(),
        "lastRow": last,
        "via": meta.get("via") or "none",
    }


def poll_once(driver, queue_name: str) -> dict:
    if not fax.queue_filter_selected(driver, queue_name):
        fax.ensure_pending_deletion_queue(driver, WebDriverWait(driver, 15), queue_name)
    refreshed = fax.click_refresh(driver)
    time.sleep(1.5)
    fax.wait_vgrid_rows(driver, timeout=10)
    fax.scroll_queue_grid(driver)
    time.sleep(0.7)
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
