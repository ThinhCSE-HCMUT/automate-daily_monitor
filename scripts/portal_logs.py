#!/usr/bin/env python3
"""
Log in to Simplifi STG Portal developer logs and download a file per IMEI.
Handles TOTP 2FA. Files land in output/.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import shutil
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime

import pyotp
from selenium.common.exceptions import (
    ElementNotInteractableException,
    InvalidSessionIdException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from chrome_util import build_chrome, chrome_binary, linux_chromedriver, temp_profile


PORTAL_HOME = "https://stg-portal.proxy.simplifi.io/"
PORTAL_URL = "https://stg-portal.proxy.simplifi.io/developer/log"


def log(level: str, msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{level}] {msg}", flush=True)


def load_conf(path: str) -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not os.path.isfile(path):
        return cfg
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            cfg[key.strip()] = val.strip()
    return cfg


def load_imeis(path: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for rec in reader:
            imei = (rec.get("data") or rec.get("imei") or rec.get("IMEI") or "").strip()
            name = (rec.get("name") or rec.get("station") or imei).strip()
            if not imei or imei.upper().startswith("REPLACE_"):
                continue
            rows.append((name, imei))
    return rows


def resolve_imeis(monitor_conf: str, imei_csv: str) -> list[tuple[str, str]]:
    """Prefer monitor.conf stations; fall back to portal_imeis.csv."""
    try:
        from stations_lib import load_stations, portal_imei_rows

        rows = portal_imei_rows(load_stations(monitor_conf))
        if rows:
            return rows
    except Exception as exc:
        log("WARN", f"Could not load stations from {monitor_conf}: {exc}")
    if os.path.isfile(imei_csv):
        return load_imeis(imei_csv)
    return []


def build_driver(download_dir: str, headed: bool, profile: str | None = None):
    if not profile:
        profile = temp_profile("simplifi-chrome-portal")
    else:
        shutil.rmtree(profile, ignore_errors=True)
        os.makedirs(profile, exist_ok=True)
        profile = os.path.abspath(profile)
    binary = chrome_binary()
    if binary:
        log("INFO", f"Chrome binary: {binary}")
    drv = linux_chromedriver()
    if drv:
        log("INFO", f"ChromeDriver: {drv}")
    else:
        log("INFO", "ChromeDriver: Selenium Manager")
    log("INFO", f"Chrome profile: {profile}")
    driver = build_chrome(headed=headed, profile=profile, download_dir=download_dir)
    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": os.path.abspath(download_dir)},
        )
    except Exception:
        pass
    return driver


def snapshot_files(folder: str) -> set[str]:
    return {os.path.abspath(p) for p in glob.glob(os.path.join(folder, "*")) if os.path.isfile(p)}


def prepare_day_dir(out_root: str, now: datetime) -> tuple[str, str]:
    """output/routers_log/28_08_2026 — wipe and recreate if it already exists."""
    folder_name = now.strftime("%d_%m_%Y")
    file_prefix = now.strftime("%d%m%Y")
    day_dir = os.path.join(os.path.abspath(out_root), "routers_log", folder_name)
    if os.path.isdir(day_dir):
        shutil.rmtree(day_dir)
        log("INFO", f"Replaced existing folder {day_dir}")
    os.makedirs(day_dir, exist_ok=True)
    log("INFO", f"Saving router logs to {day_dir}")
    return day_dir, file_prefix


def prune_old_day_dirs(out_root: str, keep: int = 15) -> None:
    base_dir = os.path.join(os.path.abspath(out_root), "routers_log")
    if not os.path.isdir(base_dir):
        return
    dated_dirs: list[tuple[datetime, str]] = []
    for name in os.listdir(base_dir):
        path = os.path.join(base_dir, name)
        if not os.path.isdir(path):
            continue
        try:
            stamp = datetime.strptime(name, "%d_%m_%Y")
        except ValueError:
            continue
        dated_dirs.append((stamp, path))
    dated_dirs.sort(key=lambda x: x[0], reverse=True)
    for _, old_path in dated_dirs[keep:]:
        shutil.rmtree(old_path, ignore_errors=True)
        log("INFO", f"Removed old router log folder {old_path}")


def wait_for_download(folder: str, before: set[str], timeout: int = 90) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        busy = glob.glob(os.path.join(folder, "*.crdownload")) + glob.glob(
            os.path.join(folder, "*.tmp")
        )
        now = snapshot_files(folder)
        added = [p for p in now - before if not p.endswith(".crdownload") and not p.endswith(".tmp")]
        if added and not busy:
            return max(added, key=os.path.getmtime)
        time.sleep(0.5)
    return None


def describe_el(el: WebElement) -> str:
    try:
        tag = el.tag_name
        typ = el.get_attribute("type") or ""
        name = el.get_attribute("name") or ""
        ph = el.get_attribute("placeholder") or ""
        cls = (el.get_attribute("class") or "")[:80]
        txt = (el.text or "").replace("\n", " ")[:60]
        return f"<{tag} type={typ!r} name={name!r} ph={ph!r} class={cls!r} text={txt!r}>"
    except Exception as exc:
        return f"<unreadable {exc}>"


def dump_page(driver, out_dir: str, label: str) -> None:
    del out_dir
    try:
        log("DEBUG", f"URL={driver.current_url} title={driver.title!r}")
    except Exception:
        pass
    try:
        inputs = driver.find_elements(By.CSS_SELECTOR, "input")
        log("DEBUG", f"Visible inputs ({len(inputs)} total):")
        shown = 0
        for el in inputs:
            if displayed(el):
                log("DEBUG", f"  input {describe_el(el)}")
                shown += 1
            if shown >= 12:
                break
        buttons = driver.find_elements(By.CSS_SELECTOR, "button, a")
        nbtn = 0
        log("DEBUG", "Visible buttons/links:")
        for el in buttons:
            if displayed(el) and ((el.text or "").strip() or el.get_attribute("aria-label")):
                log("DEBUG", f"  {describe_el(el)}")
                nbtn += 1
            if nbtn >= 15:
                break
    except Exception as exc:
        log("DEBUG", f"UI dump failed: {exc}")


def log_exc(prefix: str, exc: BaseException) -> None:
    log("ERROR", f"{prefix}: {type(exc).__name__}: {exc}")
    tb = traceback.format_exc()
    for line in tb.strip().splitlines()[-12:]:
        log("ERROR", f"  {line}")


def displayed(el: WebElement) -> bool:
    try:
        return el.is_displayed() and el.is_enabled()
    except Exception:
        return False


def session_dead(exc: BaseException) -> bool:
    if isinstance(exc, InvalidSessionIdException):
        return True
    text = str(exc).lower()
    return "invalid session" in text or "chrome not reachable" in text


def first_visible(driver, selectors: list[str]) -> WebElement | None:
    for css in selectors:
        for el in driver.find_elements(By.CSS_SELECTOR, css):
            if displayed(el):
                return el
    return None


def js_set_value(driver, el: WebElement, value: str) -> None:
    """Set input value the way React/Ant Design actually listens (native setter)."""
    driver.execute_script(
        """
        const el = arguments[0];
        const val = arguments[1];
        el.scrollIntoView({block: 'center'});
        el.removeAttribute('readonly');
        el.disabled = false;
        el.focus();
        const proto = window.HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) {
            desc.set.call(el, '');
            desc.set.call(el, val);
        } else {
            el.value = val;
        }
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        try {
            el.dispatchEvent(new InputEvent('input', {bubbles: true, data: val, inputType: 'insertText'}));
        } catch (e) {}
        """,
        el,
        value,
    )


def fill_interactable(driver, el: WebElement, value: str) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.2)
    try:
        WebDriverWait(driver, 8).until(EC.element_to_be_clickable(el))
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    try:
        el.clear()
    except Exception:
        pass
    js_set_value(driver, el, value)
    # Do not send_keys(ENTER) here — on Pi Chromium it often crashes or is ignored by React.


def click_submit_like(driver) -> None:
    xpaths = [
        "//button[@type='submit']",
        "//button[.//span[normalize-space()='Verify']]",
        "//button[.//span[normalize-space()='Continue']]",
        "//button[.//span[normalize-space()='Confirm']]",
        "//button[.//span[normalize-space()='Submit']]",
        "//button[normalize-space()='Verify']",
        "//button[normalize-space()='Continue']",
        "//button[normalize-space()='Submit']",
        "//button[.//span[normalize-space()='Sign In']]",
        "//button[.//span[normalize-space()='Log in']]",
        "//button[normalize-space()='Sign In']",
        "//button[normalize-space()='Login']",
    ]
    for xp in xpaths:
        for el in driver.find_elements(By.XPATH, xp):
            if displayed(el):
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                return


def page_url(driver) -> str:
    try:
        return driver.current_url or ""
    except Exception:
        return ""


def on_2fa_page(driver) -> bool:
    url = page_url(driver)
    if "/2fa/" in url:
        return True
    return len(otp_cells(driver)) >= 4


def on_log_page(driver) -> bool:
    url = page_url(driver)
    if "/login" in url or "/2fa/" in url:
        return False
    return "developer/log" in url


def otp_cells(driver) -> list[WebElement]:
    seen: set[str] = set()
    cells: list[WebElement] = []
    for sel in ("input.ant-otp-input", ".ant-otp input"):
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            if not displayed(el):
                continue
            key = el.id
            if key in seen:
                continue
            seen.add(key)
            cells.append(el)
    return cells


def is_log_search_box(el: WebElement) -> bool:
    try:
        cls = (el.get_attribute("class") or "").lower()
        eid = (el.get_attribute("id") or "").lower()
        typ = (el.get_attribute("type") or "").lower()
        mx = el.get_attribute("maxlength") or ""
    except Exception:
        return False
    if "otp" in cls or eid in ("username", "password") or typ == "password":
        return False
    if mx.strip() == "1":
        return False
    return displayed(el)


def wait_post_login(driver, timeout: int = 25) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = page_url(driver)
        if on_2fa_page(driver):
            log("INFO", f"Landed on 2FA  URL={url}")
            return "2fa"
        if on_log_page(driver):
            log("INFO", f"Landed on log page  URL={url}")
            return "log"
        time.sleep(0.4)
    log("WARN", f"Post-login still at URL={page_url(driver)}")
    return "unknown"


def totp_code(secret: str) -> str:
    totp = pyotp.TOTP(secret.replace(" ", "").replace("-", ""))
    remaining = totp.interval - (int(time.time()) % totp.interval)
    if remaining <= 3:
        log("INFO", f"TOTP expires in {remaining}s — waiting for next code")
        time.sleep(remaining + 0.4)
    code = totp.now()
    log("INFO", f"Using TOTP (valid ~{totp.interval - (int(time.time()) % totp.interval)}s)")
    return code


def fill_otp_cells(driver, cells: list[WebElement], code: str) -> None:
    code = (code or "")[:6]
    log("INFO", f"Filling {len(cells)} OTP cell(s)")
    try:
        cells[0].click()
        cells[0].send_keys(Keys.CONTROL, "a")
        cells[0].send_keys(Keys.BACKSPACE)
        cells[0].send_keys(code)
    except Exception as exc:
        log("DEBUG", f"Bulk OTP send_keys failed: {type(exc).__name__}: {exc}")
    values = [(c.get_attribute("value") or "") for c in cells]
    log("INFO", f"OTP after bulk fill: {values}")
    if "".join(values) != code and len(cells) >= 6:
        for i, ch in enumerate(code[: len(cells)]):
            try:
                cells[i].click()
                cells[i].send_keys(Keys.BACKSPACE)
                cells[i].send_keys(ch)
            except Exception:
                js_set_value(driver, cells[i], ch)
        values = [(c.get_attribute("value") or "") for c in cells]
        log("INFO", f"OTP after per-digit fill: {values}")


def click_verify(driver) -> bool:
    xpaths = [
        "//button[@type='submit']",
        "//button[.//span[normalize-space()='Verify']]",
        "//button[normalize-space()='Verify']",
    ]
    for xp in xpaths:
        for el in driver.find_elements(By.XPATH, xp):
            if displayed(el):
                click_el(driver, el, "Verify")
                return True
    log("WARN", "Verify button not found")
    return False


def complete_2fa(driver, secret: str, attempts: int = 3) -> bool:
    deadline = time.time() + 20
    cells: list[WebElement] = []
    while time.time() < deadline:
        cells = otp_cells(driver)
        if len(cells) >= 6:
            break
        time.sleep(0.3)
    if len(cells) < 6:
        log("ERROR", f"2FA page has {len(cells)} OTP cell(s), expected 6  URL={page_url(driver)}")
        return False

    for attempt in range(1, attempts + 1):
        for el in driver.find_elements(By.XPATH, "//button[normalize-space()='Close']"):
            if displayed(el):
                click_el(driver, el, "Close 2FA error")
                time.sleep(0.4)
        cells = otp_cells(driver) or cells
        code = totp_code(secret)
        log("INFO", f"2FA attempt {attempt}/{attempts}")
        fill_otp_cells(driver, cells, code)
        click_verify(driver)
        end = time.time() + 12
        while time.time() < end:
            if on_log_page(driver) or ("/2fa/" not in page_url(driver) and "/login" not in page_url(driver)):
                log("PASSED", f"2FA accepted  URL={page_url(driver)}")
                return True
            time.sleep(0.4)
        log("WARN", f"Still on 2FA after attempt {attempt}  URL={page_url(driver)}")
        time.sleep(1.2)
    return False


def log_search_selectors() -> list[str]:
    return [
        "span.ant-input-group-wrapper input.ant-input",
        ".ant-input-search input.ant-input",
        "input.ant-input-lg",
        "input[placeholder*='IMEI' i]",
        "input[placeholder*='imei' i]",
        "input[placeholder*='search' i]",
        "input.ant-input[type='text']",
    ]


def wait_log_search(driver, timeout: int) -> WebElement:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        if on_2fa_page(driver):
            last_err = "still on 2FA page"
            time.sleep(0.4)
            continue
        for css in log_search_selectors():
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                if not is_log_search_box(el):
                    continue
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    if el.is_displayed() and el.is_enabled():
                        return el
                except Exception as exc:
                    last_err = exc
        time.sleep(0.4)
    raise TimeoutException(f"Log search box not interactable: {last_err} URL={page_url(driver)}")


def click_el(driver, el: WebElement, what: str) -> None:
    log("INFO", f"Click {what}: {describe_el(el)}")
    try:
        el.click()
    except Exception as exc:
        log("DEBUG", f"Native click failed ({type(exc).__name__}), using JS click")
        driver.execute_script("arguments[0].click();", el)


def click_search_button(driver) -> bool:
    locators = [
        (By.CSS_SELECTOR, "button.ant-input-search-button"),
        (By.CSS_SELECTOR, ".ant-input-search-button"),
        (By.XPATH, "//button[.//span[contains(@class,'anticon-search')]]"),
        (By.XPATH, "//button[contains(@class,'ant-input-search-button')]"),
        (By.XPATH, "//button[.//span[normalize-space()='Search']]"),
        (By.XPATH, "//span[normalize-space()='Search']/ancestor::button"),
    ]
    for by, sel in locators:
        for el in driver.find_elements(by, sel):
            if displayed(el):
                click_el(driver, el, "Search")
                return True
    log("WARN", "No Search button found — submitting the nearest form")
    driver.execute_script(
        """
        const el = document.querySelector('input.ant-input, input[type="text"]');
        if (!el) return;
        const form = el.closest('form');
        if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return; }
        el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}));
        el.dispatchEvent(new KeyboardEvent('keyup', {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}));
        """
    )
    return False


def wait_search_settled(driver, timeout: int = 20) -> None:
    try:
        WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".ant-spin-spinning, .ant-spin-dot"))
        )
        log("INFO", "Search spinner appeared, waiting for it to finish ...")
    except TimeoutException:
        log("DEBUG", "No spinner seen after search")
    try:
        WebDriverWait(driver, timeout).until_not(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".ant-spin-spinning"))
        )
    except TimeoutException:
        log("WARN", "Spinner still visible after wait")


def find_download_button(driver) -> WebElement | None:
    locators = [
        (By.XPATH, "//button[.//span[contains(normalize-space(),'Download')]]"),
        (By.XPATH, "//button[contains(normalize-space(),'Download')]"),
        (By.XPATH, "//a[contains(normalize-space(),'Download')]"),
        (By.XPATH, "//span[contains(normalize-space(),'Download')]/ancestor::*[self::button or self::a][1]"),
        (By.CSS_SELECTOR, "button[aria-label*='Download'], a[aria-label*='Download']"),
        (By.CSS_SELECTOR, "a[download], a[href*='download'], a[href*='.zip'], a[href*='.log']"),
        (By.XPATH, "//button[contains(@class,'ant-btn-primary')]"),
    ]
    for by, sel in locators:
        for el in driver.find_elements(by, sel):
            if not displayed(el):
                continue
            text = ((el.text or "") + " " + (el.get_attribute("aria-label") or "")).lower()
            if "download" in text or ("download" in sel.lower() and "ant-btn-primary" not in sel):
                return el
    return None


def maybe_enter_iframe(driver) -> None:
    driver.switch_to.default_content()
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    log("DEBUG", f"Page has {len(frames)} iframe(s)")
    for i, fr in enumerate(frames):
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(fr)
            for css in log_search_selectors():
                for el in driver.find_elements(By.CSS_SELECTOR, css):
                    if is_log_search_box(el):
                        log("INFO", f"Search box is inside iframe #{i}")
                        return
        except Exception as exc:
            log("DEBUG", f"iframe #{i} skipped: {type(exc).__name__}")
    driver.switch_to.default_content()


def pick_suggestion(driver, imei: str) -> bool:
    deadline = time.time() + 3
    xps = [
        f"//div[contains(@class,'ant-select-item') and contains(., '{imei}')]",
        f"//div[contains(@class,'rc-virtual-list')]//*[contains(normalize-space(), '{imei}')]",
        f"//li[contains(@class,'ant-select-item') and contains(., '{imei}')]",
        f"//div[contains(@class,'ant-dropdown') or contains(@class,'ant-select-dropdown')]//*[contains(., '{imei}')]",
    ]
    while time.time() < deadline:
        for xp in xps:
            for el in driver.find_elements(By.XPATH, xp):
                if displayed(el):
                    click_el(driver, el, "IMEI suggestion")
                    return True
        time.sleep(0.3)
    return False


def search_and_download(driver, _wait: WebDriverWait, imei: str, _debug_dir: str) -> None:
    if on_2fa_page(driver):
        raise TimeoutException(f"Still on 2FA page, cannot search IMEI. URL={page_url(driver)}")
    log("INFO", f"Step 1/5 locate search box  URL={page_url(driver)}")
    box = wait_log_search(driver, 20)
    log("INFO", f"Step 1/5 search box: {describe_el(box)}")

    log("INFO", f"Step 2/5 fill IMEI {imei}")
    fill_interactable(driver, box, imei)
    try:
        box.send_keys(Keys.CONTROL + "a")
        box.send_keys(imei)
    except Exception as exc:
        log("DEBUG", f"send_keys fill skipped: {type(exc).__name__}")
    shown = box.get_attribute("value") or ""
    log("INFO", f"Step 2/5 input value now={shown!r}")
    if shown != imei:
        log("WARN", "React did not keep the IMEI — retrying native setter")
        js_set_value(driver, box, imei)
        shown = box.get_attribute("value") or ""
        log("INFO", f"Step 2/5 retry value={shown!r}")

    if pick_suggestion(driver, imei):
        log("INFO", "Step 2b picked IMEI from dropdown")
    else:
        log("DEBUG", "No autocomplete dropdown — continuing with Search click")

    log("INFO", "Step 3/5 click Search (not Enter — Enter is unreliable on Pi Chromium)")
    click_search_button(driver)
    wait_search_settled(driver, 20)

    empty = first_visible(driver, [".ant-empty", ".ant-empty-description", ".ant-table-placeholder"])
    if empty:
        log("WARN", f"Search looks empty: {describe_el(empty)}")

    log("INFO", "Step 4/5 wait for Download button")
    btn = None
    deadline = time.time() + 25
    while time.time() < deadline:
        btn = find_download_button(driver)
        if btn:
            break
        time.sleep(0.4)
    if not btn:
        dump_page(driver, _debug_dir, f"no_download_{imei}")
        raise TimeoutException(
            "No Download button after search. "
            "Search likely did not run or the page layout changed."
        )
    log("INFO", f"Step 4/5 Download button: {describe_el(btn)}")

    log("INFO", "Step 5/5 click Download")
    click_el(driver, btn, "Download")


def pi_ip() -> str:
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True).strip()
        if out:
            return out.split()[0]
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "<pi-ip>"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", default="portal.conf")
    parser.add_argument("--out", default="output")
    parser.add_argument("--monitor-conf", default="monitor.conf")
    parser.add_argument("--imei-csv", default="scripts/portal_imeis.csv")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    cfg = load_conf(args.conf)
    email = cfg.get("email") or cfg.get("username") or ""
    password = cfg.get("password") or ""
    totp_secret = cfg.get("totp_secret") or cfg.get("otp_secret") or ""
    headed = args.headed or cfg.get("headless", "1") in ("0", "false", "no")
    login_wait = int(cfg.get("login_wait_sec") or "40")
    portal_url = cfg.get("portal_url") or PORTAL_URL

    if not email or not password:
        log("ERROR", f"Fill email= and password= in {args.conf}")
        return 2
    if not totp_secret:
        log("ERROR", f"Fill totp_secret= in {args.conf} (STG 2FA)")
        return 2

    imeis = resolve_imeis(args.monitor_conf, args.imei_csv)
    if not imeis:
        log("ERROR", f"No IMEIs in {args.monitor_conf} or {args.imei_csv}")
        return 2
    log("INFO", f"Portal IMEI list from {'monitor.conf' if os.path.isfile(args.monitor_conf) else args.imei_csv}: {len(imeis)} station(s)")

    os.makedirs(args.out, exist_ok=True)
    now = datetime.now()
    download_dir, file_prefix = prepare_day_dir(args.out, now)
    saved: list[str] = []
    chrome_profile = temp_profile("simplifi-chrome-portal")

    log("INFO", f"Opening STG portal {portal_url} for {len(imeis)} IMEI(s)  headless={not headed}")
    driver = build_driver(download_dir, headed, profile=chrome_profile)
    wait = WebDriverWait(driver, 30)
    try:
        log("INFO", "Login step: load page")
        driver.get(portal_url)
        wait.until(EC.presence_of_element_located((By.ID, "username")))
        log("INFO", f"Login step: fill username  URL={driver.current_url}")
        user_el = driver.find_element(By.ID, "username")
        fill_interactable(driver, user_el, email)
        pass_el = driver.find_element(By.ID, "password")
        fill_interactable(driver, pass_el, password)
        click_submit_like(driver)
        log("INFO", "Login step: credentials submitted, waiting for 2FA or log page ...")
        where = wait_post_login(driver, 25)
        if where == "2fa" or on_2fa_page(driver):
            if not complete_2fa(driver, totp_secret):
                dump_page(driver, "", "2fa_failed")
                log("ERROR", "2FA failed — cannot open developer logs")
                return 1
            time.sleep(1)
        if not on_log_page(driver):
            log("INFO", f"Opening log page after auth  URL={page_url(driver)}")
            driver.get(portal_url)
            where = wait_post_login(driver, 20)
            if where == "2fa" or on_2fa_page(driver):
                if not complete_2fa(driver, totp_secret):
                    dump_page(driver, "", "2fa_failed_retry")
                    log("ERROR", "2FA failed on log page redirect")
                    return 1

        log("INFO", f"Waiting for log search box (up to {login_wait}s)  URL={page_url(driver)}")
        maybe_enter_iframe(driver)
        wait_log_search(driver, login_wait)
        if not on_log_page(driver):
            dump_page(driver, "", "not_log_page")
            raise TimeoutException(
                f"Expected developer/log, still at {page_url(driver)} — 2FA was not completed"
            )
        log("PASSED", "Log page ready")
        dump_page(driver, "", "log_page_ready")

        for name, imei in imeis:
            log("INFO", f"======== {name} ({imei}) ========")
            before = snapshot_files(download_dir)
            try:
                search_and_download(driver, wait, imei, "")
            except Exception as exc:
                log_exc(imei, exc)
                dump_page(driver, "", f"fail_{imei}")
                if session_dead(exc):
                    log("ERROR", "Browser session died — stopping remaining IMEIs")
                    break
                continue
            log("INFO", f"Waiting for file in {download_dir} ...")
            path = wait_for_download(download_dir, before, timeout=90)
            if not path:
                log("ERROR", f"Download timed out for {imei} — no new file in {download_dir}")
                dump_page(driver, "", f"dl_timeout_{imei}")
                continue
            dest = os.path.join(download_dir, f"{file_prefix}_{imei}.log")
            if os.path.abspath(path) != os.path.abspath(dest):
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(path, dest)
            saved.append(dest)
            log("PASSED", f"Saved {dest}")
    except Exception as exc:
        log_exc("portal", exc)
        try:
            dump_page(driver, "", "fatal")
        except Exception:
            pass
        raise
    finally:
        driver.quit()

    prune_old_day_dirs(args.out, keep=15)

    host = pi_ip()
    print("", flush=True)
    print("=" * 62, flush=True)
    print("Portal logs are on the Raspberry Pi. From your Windows laptop:", flush=True)
    print("", flush=True)
    print(f"  scp -r pi@{host}:\"{download_dir}\" .", flush=True)
    print("", flush=True)
    if saved:
        print("Files:", flush=True)
        for p in saved:
            print(f"  - {p}", flush=True)
    else:
        print("No files were downloaded.", flush=True)
    print("=" * 62, flush=True)
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
