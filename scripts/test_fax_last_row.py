#!/usr/bin/env python3
"""
Laptop: print Received On + User of the LAST queue row after Refresh.

Does NOT send a fax. After login, opens ReceivedPendingDeletion, clicks
Refresh, jumps to the last row, prints fields (12h cloud vs 24h monitor).

  python scripts/test_fax_last_row.py
  python scripts/test_fax_last_row.py --watch
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.chdir(ROOT)

from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

import send_fax as fax  # noqa: E402
from fax_queue_match import (  # noqa: E402
    clocks_as_24h,
    describe_last_row,
    extract_received_on,
    extract_user,
)


def read_last(driver, queue_name: str) -> tuple[str, dict]:
    if not fax.queue_filter_selected(driver, queue_name):
        fax.ensure_pending_deletion_queue(driver, WebDriverWait(driver, 15), queue_name)
    fax.click_refresh(driver)
    time.sleep(1.5)
    n = fax.wait_vgrid_rows(driver, timeout=12)
    fax.log("INFO", f"v-grid-row count after refresh={n}")
    rows: list[str] = []
    meta: dict = {}
    for _ in range(8):
        fax.scroll_queue_grid(driver)
        time.sleep(0.7)
        rows, meta = fax.collect_queue_rows(driver, "pair")
        if int(meta.get("n") or 0) > 0 and (rows or meta.get("lastRow")):
            break
    last = meta.get("lastRow") or (rows[-1] if rows else "")
    return last, meta


def print_last(last: str, meta: dict, when: datetime) -> None:
    rec = meta.get("lastReceivedOn") or extract_received_on(last) or "-"
    user = meta.get("lastUser") or extract_user(last) or "-"
    parsed = ",".join(clocks_as_24h(last)) or "none"
    fax.log(
        "INFO",
        f"Grid n={meta.get('n')} via={meta.get('via')} maxTop={meta.get('maxTop')}",
    )
    fax.log("INFO", f"Last User         = {user}")
    fax.log("INFO", f"Last Received On  = {rec}")
    fax.log("INFO", f"Parsed to 24h     = {parsed}   (05:24pm → 17:24)")
    fax.log("INFO", f"Laptop/Pi now 24h = {when.strftime('%H:%M:%S')}")
    fax.log("INFO", describe_last_row(last, when))
    if last:
        fax.log("DEBUG", f"raw: {last[:240]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", default="fax.conf")
    parser.add_argument("--watch", action="store_true", help="Refresh every 5s")
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

    fax.log("INFO", "No fax will be sent — last-row Received On + User")
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
        fax.open_received_pending_queue(driver, wait, queue_name)

        last, meta = read_last(driver, queue_name)
        print_last(last, meta, datetime.now())
        if not last:
            fax.log("FAILED", "Could not read last row after jumping to end")
            return 1

        if not args.watch:
            return 0
        fax.log("INFO", "Watching; Ctrl+C to stop")
        while True:
            time.sleep(max(args.interval, 1))
            last, meta = read_last(driver, queue_name)
            print_last(last, meta, datetime.now())
    except KeyboardInterrupt:
        fax.log("INFO", "Stopped")
        return 0
    except Exception as exc:
        fax.log("ERROR", f"{type(exc).__name__}: {exc}")
        try:
            fax.dump_shot(driver, "last_row_fail")
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
