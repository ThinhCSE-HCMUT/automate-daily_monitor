#!/usr/bin/env python3
"""
Log in to Faxback Cloud, send a test document to the Fax Stations, then
confirm delivery on Queues → ReceivedPendingDeletion.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import datetime

from selenium.common.exceptions import TimeoutException, UnexpectedAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from chrome_util import build_chrome, chrome_binary, linux_chromedriver, temp_profile


FAX_URL = "https://cloud.faxback.net/faxadmin/"
DEFAULT_NUMBERS = ["84352738502", "84388164291"]
# User on Faxback ReceivedPendingDeletion → router IMEI in daily_monitor.csv
DEFAULT_QUEUE_USERS = (
    ("simplifivn1", "861107035990853"),
    ("simplifivn2", "866758040526465"),
)
CHROME_PROFILE = ""
STATUS_FILE = "output/fax_status.txt"

SCAN_ROWS_JS = """
const users = arguments[0];
const dates = arguments[1];
const found = {};
users.forEach((u) => { found[u] = false; });
const samples = [];

function dateHit(text) {
  const t = text || "";
  const tl = t.toLowerCase();
  for (const d of dates) {
    if (!d) continue;
    if (t.indexOf(d) !== -1 || tl.indexOf(String(d).toLowerCase()) !== -1)
      return true;
  }
  return false;
}

function userFromRow(text) {
  const t = (text || "").replace(/\\s+/g, " ");
  if (!dateHit(t)) return null;
  const tl = t.toLowerCase();
  let matched = null;
  for (const u of users) {
    if (tl.indexOf(String(u).toLowerCase()) !== -1) {
      matched = u;
      break;
    }
  }
  if (!matched) return null;
  if (/hung up|poor line|no answer|busy|failed/i.test(t) && !/success/i.test(t))
    return null;
  return matched;
}

function mark(text) {
  const u = userFromRow(text);
  if (u) found[u] = true;
}

const rowNodes = document.querySelectorAll('tr, [role="row"]');
for (const n of rowNodes)
  mark(n.innerText || n.textContent || "");

const cells = document.querySelectorAll(".jqx-grid-cell, .jqx-grid-cell-left-align, .jqx-grid-cell-pinned");
if (cells.length) {
  const byRow = {};
  for (const c of cells) {
    const r = c.getBoundingClientRect();
    const key = c.getAttribute("row") || c.getAttribute("data-row")
      || String(Math.round(r.top / 8) * 8);
    byRow[key] = (byRow[key] || "") + " " + (c.innerText || c.textContent || "");
  }
  Object.keys(byRow).forEach((k) => {
    if (samples.length < 6) samples.push(byRow[k].replace(/\\s+/g, " ").trim().slice(0, 140));
    mark(byRow[k]);
  });
}

if (!users.some((u) => found[u])) {
  const body = document.body ? (document.body.innerText || "") : "";
  users.forEach((u) => {
    const low = body.toLowerCase();
    const idx = low.indexOf(String(u).toLowerCase());
    if (idx < 0) return;
    const windowText = body.slice(Math.max(0, idx - 240), idx + 240);
    mark(windowText);
  });
}
return {found: found, samples: samples, cells: cells.length};
"""


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


def dump_shot(driver, label: str) -> None:
    try:
        os.makedirs("output/debug", exist_ok=True)
        png = os.path.join("output", "debug", f"fax_{label}_{datetime.now().strftime('%H%M%S')}.png")
        driver.save_screenshot(png)
        log("DEBUG", f"Screenshot: {png}  URL={driver.current_url}")
    except Exception as exc:
        log("DEBUG", f"Screenshot failed: {exc}")


def accept_alerts(driver) -> None:
    for _ in range(3):
        try:
            alert = driver.switch_to.alert
            log("INFO", f"Alert: {alert.text}")
            alert.accept()
            time.sleep(0.4)
        except Exception:
            break


def build_driver(headed: bool):
    global CHROME_PROFILE
    CHROME_PROFILE = temp_profile("simplifi-chrome-fax")
    binary = chrome_binary()
    if binary:
        log("INFO", f"Chrome binary: {binary}")
    drv = linux_chromedriver()
    if drv:
        log("INFO", f"ChromeDriver: {drv}")
    else:
        log("INFO", "ChromeDriver: Selenium Manager")
    log("INFO", f"Chrome profile: {CHROME_PROFILE}")
    return build_chrome(headed=headed, profile=CHROME_PROFILE)


def click_id(driver, wait: WebDriverWait, elem_id: str) -> None:
    el = wait.until(EC.element_to_be_clickable((By.ID, elem_id)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    accept_alerts(driver)


def write_fax_txt(path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = (
        "========================================\n"
        "  SIMPLIFI DAILY MONITOR - FAX TEST\n"
        "========================================\n"
        "\n"
        f"Sent: {stamp}\n"
        "From: Raspberry Pi daily monitor\n"
        "\n"
        "This page was sent automatically after the\n"
        "SSH router check and portal log download.\n"
        "\n"
        "If this prints on the Fax Station, the fax\n"
        "path is working.\n"
        "\n"
        "========================================\n"
    )
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(body)
    abs_path = os.path.abspath(path)
    log("INFO", f"Fax document written: {abs_path} ({os.path.getsize(abs_path)} bytes)")
    return abs_path


def find_file_input(driver):
    driver.switch_to.default_content()
    inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
    if inputs:
        return inputs[0]
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    log("DEBUG", f"No file input on main page; {len(frames)} iframe(s)")
    for frame in frames:
        driver.switch_to.default_content()
        try:
            driver.switch_to.frame(frame)
            inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
            if inputs:
                return inputs[0]
        except Exception:
            continue
    driver.switch_to.default_content()
    return None


def page_has(driver, *needles: str) -> bool:
    try:
        src = (driver.page_source or "").lower()
    except UnexpectedAlertPresentException:
        accept_alerts(driver)
        src = (driver.page_source or "").lower()
    return any(n.lower() in src for n in needles)


def wait_attachment_listed(driver, filename: str, timeout: int = 25) -> bool:
    base = os.path.basename(filename)
    deadline = time.time() + timeout
    while time.time() < deadline:
        accept_alerts(driver)
        if page_has(driver, base, "fax_message", ".txt"):
            return True
        time.sleep(0.5)
    return False


def parse_numbers(raw: str) -> list[str]:
    nums: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        n = "".join(ch for ch in part.strip() if ch.isdigit())
        if n:
            nums.append(n)
    return nums or list(DEFAULT_NUMBERS)


def parse_queue_users(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if ":" not in part:
            continue
        user, imei = part.split(":", 1)
        user, imei = user.strip(), imei.strip()
        if user and imei:
            pairs.append((user, imei))
    return pairs or list(DEFAULT_QUEUE_USERS)


def date_needles(now: datetime) -> list[str]:
    day = str(now.day)
    month_abbr = now.strftime("%b")
    month_full = now.strftime("%B")
    needles = [
        f"{month_abbr} {day}, {now.year}",
        f"{month_abbr} {now.day:02d}, {now.year}",
        f"{month_full} {day}, {now.year}",
        now.strftime("%Y-%m-%d"),
        now.strftime("%m/%d/%Y"),
        f"{now.month}/{now.day}/{now.year}",
        now.strftime("%d/%m/%Y"),
        f"{now.day:02d}/{now.month:02d}/{now.year}",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for n in needles:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def write_status(path: str, found: dict[str, bool], users: list[tuple[str, str]]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# imei=PASS|FAIL from Faxback ReceivedPendingDeletion\n")
        for user, imei in users:
            f.write(f"{imei}={'PASS' if found.get(user) else 'FAIL'}\n")
    summary = ", ".join(
        f"{user}({imei})={'PASS' if found.get(user) else 'FAIL'}" for user, imei in users
    )
    log("INFO", f"Wrote fax status {path}: {summary}")


def click_nav(driver, wait: WebDriverWait, ids: tuple[str, ...], texts: tuple[str, ...]) -> str:
    driver.switch_to.default_content()
    accept_alerts(driver)
    for elem_id in ids:
        if driver.find_elements(By.ID, elem_id):
            click_id(driver, wait, elem_id)
            return elem_id
    for text in texts:
        for by, value in (
            (By.LINK_TEXT, text),
            (By.PARTIAL_LINK_TEXT, text),
            (By.XPATH, f"//a[normalize-space()='{text}']"),
            (By.XPATH, f"//*[self::a or self::span or self::div or self::td]"
                       f"[normalize-space()='{text}']"),
        ):
            els = driver.find_elements(by, value)
            if not els:
                continue
            el = els[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            accept_alerts(driver)
            return text
    raise TimeoutException(f"Nav not found: ids={ids} texts={texts}")


def click_matching(driver, xpaths: list[str]) -> bool:
    for _ in iter_frames(driver):
        for xp in xpaths:
            els = driver.find_elements(By.XPATH, xp)
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    try:
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    accept_alerts(driver)
                    driver.switch_to.default_content()
                    return True
                except Exception:
                    continue
    driver.switch_to.default_content()
    return False


def iter_frames(driver, max_depth: int = 6):
    """Yield default document, then every iframe/frame including nested ones."""

    def rec(depth: int):
        yield
        if depth >= max_depth:
            return
        try:
            n = len(driver.find_elements(By.CSS_SELECTOR, "iframe, frame"))
        except Exception:
            return
        for i in range(n):
            try:
                frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
                driver.switch_to.frame(frames[i])
            except Exception:
                continue
            yield from rec(depth + 1)
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                return

    driver.switch_to.default_content()
    yield from rec(0)
    driver.switch_to.default_content()


def select_native_option(driver, needle: str) -> bool:
    want = needle.lower()
    for _ in iter_frames(driver):
        for sel in driver.find_elements(By.TAG_NAME, "select"):
            try:
                for opt in Select(sel).options:
                    label = f"{opt.text} {opt.get_attribute('value') or ''}".lower()
                    if want in label:
                        Select(sel).select_by_visible_text(opt.text)
                        accept_alerts(driver)
                        driver.switch_to.default_content()
                        return True
            except Exception:
                continue
    driver.switch_to.default_content()
    return False


def select_by_text(driver, needle: str) -> bool:
    """Pick a native <select> option or click a matching dropdown item."""
    if select_native_option(driver, needle):
        return True
    driver.switch_to.default_content()
    want = needle.lower()
    js = """
    const want = arguments[0].toLowerCase();
    const compact = want.replace(/\\s+/g, '');
    const nodes = document.querySelectorAll('div, span, li, a, option, label');
    for (const n of nodes) {
      const t = (n.innerText || n.textContent || '').trim();
      if (!t || t.length > 64) continue;
      const tl = t.toLowerCase();
      if (tl === want || tl.replace(/\\s+/g, '') === compact) {
        n.click();
        return t;
      }
    }
    return '';
    """
    for _ in iter_frames(driver):
        try:
            hit = driver.execute_script(js, needle)
            if hit:
                accept_alerts(driver)
                driver.switch_to.default_content()
                return True
        except Exception:
            continue
    driver.switch_to.default_content()
    return click_matching(
        driver,
        [
            f"//option[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{want}')]",
        ],
    )


def queue_dropdown_text(driver) -> str:
    """Visible Queue: jqx value (Received / ReceivedPendingDeletion / …)."""
    js = """
    function compact(s) { return (s || '').replace(/\\s+/g, '').toLowerCase(); }
    const skip = {allusers:1, viewfaxesfor:1, queue:1};
    const texts = [];
    document.querySelectorAll('.jqx-dropdownlist-content, .jqx-combobox-input').forEach((n) => {
      const t = (n.value || n.innerText || n.textContent || '').trim().split('\\n')[0].trim();
      if (t && !skip[compact(t)]) texts.push(t);
    });
    return texts.length ? texts[0] : '';
    """
    for _ in iter_frames(driver):
        try:
            text = (driver.execute_script(js) or "").strip()
            if text:
                driver.switch_to.default_content()
                return text
        except Exception:
            continue
    driver.switch_to.default_content()
    return ""


def log_queue_toolbar(driver) -> None:
    js = """
    function compact(s) { return (s || '').replace(/\\s+/g, ' ').trim(); }
    const drops = Array.from(document.querySelectorAll(
      '.jqx-dropdownlist-content, .jqx-combobox-input'))
      .map((n) => compact(n.innerText || n.value || '').split('\\n')[0])
      .filter(Boolean);
    const btns = Array.from(document.querySelectorAll(
      '.jqx-button, button, input[type=button], input[type=submit], [role=button]'))
      .map((n) => compact((n.innerText || n.value || n.title || '')).split('\\n')[0])
      .filter((t) => t && t.length < 32);
    return {drops, btns: Array.from(new Set(btns))};
    """
    all_drops: list[str] = []
    all_btns: list[str] = []
    for _ in iter_frames(driver):
        try:
            info = driver.execute_script(js) or {}
            all_drops.extend(info.get("drops") or [])
            all_btns.extend(info.get("btns") or [])
        except Exception:
            continue
    driver.switch_to.default_content()
    log("INFO", f"Queues toolbar dropdowns={all_drops} buttons={all_btns}")


def queue_filter_selected(driver, queue_name: str) -> bool:
    shown = queue_dropdown_text(driver).replace(" ", "").lower()
    want = (queue_name or "").replace(" ", "").lower()
    return bool(want) and shown == want


def select_jqx_queue(driver, queue_name: str) -> bool:
    """Open the Queue jqx list (not View Faxes For) and pick ReceivedPendingDeletion."""
    if select_native_option(driver, queue_name):
        return True
    js_jqx_api = """
    const want = (arguments[0] || '').replace(/\\s+/g, '').toLowerCase();
    if (!(window.jQuery && jQuery.fn && jQuery.fn.jqxDropDownList)) return '';
    const hosts = jQuery('.jqx-dropdownlist');
    for (let i = 0; i < hosts.length; i++) {
      try {
        const el = hosts.eq(i);
        const shown = ((el.jqxDropDownList('val') || el.text() || '') + '').replace(/\\s+/g, '').toLowerCase();
        if (shown.indexOf('alluser') !== -1) continue;
        const items = el.jqxDropDownList('getItems') || [];
        for (const it of items) {
          const label = ((it && (it.label || it.value)) || '') + '';
          const c = label.replace(/\\s+/g, '').toLowerCase();
          if (c === want || (c.indexOf('receivedpending') !== -1 && want.indexOf('receivedpending') !== -1)) {
            el.jqxDropDownList('selectItem', it);
            return label || 'api';
          }
        }
      } catch (e) {}
    }
    return '';
    """
    js_open = """
    function compact(s) { return (s || '').replace(/\\s+/g, '').toLowerCase(); }
    const lists = Array.from(document.querySelectorAll('.jqx-dropdownlist'));
    for (const host of lists) {
      const content = host.querySelector('.jqx-dropdownlist-content') || host;
      const t = compact(content.innerText || content.textContent || '');
      if (!t || t.indexOf('alluser') !== -1) continue;
      const clickable = host.querySelector(
        '.jqx-dropdownlist-content, .jqx-icon-arrow-down, .jqx-dropdownlist-arrow') || host;
      clickable.click();
      return content.innerText || 'opened';
    }
    return '';
    """
    js_pick = """
    const want = (arguments[0] || '').replace(/\\s+/g, '').toLowerCase();
    const items = document.querySelectorAll(
      '.jqx-listitem-element, .jqx-item, .jqx-listitem-state-normal, .jqx-popup .jqx-listitem-element, .jqx-listbox div, .jqx-listbox span, li');
    for (const n of items) {
      const t = (n.innerText || n.textContent || '').trim().split('\\n')[0].trim();
      if (!t || t.length > 80) continue;
      const c = t.replace(/\\s+/g, '').toLowerCase();
      if (c === want || (want.indexOf('receivedpending') !== -1 && c.indexOf('receivedpending') !== -1)) {
        n.click();
        return t;
      }
    }
    return '';
    """
    for _ in iter_frames(driver):
        try:
            hit = driver.execute_script(js_jqx_api, queue_name)
            if hit:
                accept_alerts(driver)
                driver.switch_to.default_content()
                log("INFO", f"jqx API selected queue {hit}")
                return True
        except Exception:
            continue
    driver.switch_to.default_content()

    opened = ""
    for _ in iter_frames(driver):
        try:
            opened = driver.execute_script(js_open) or ""
            if opened:
                break
        except Exception:
            continue
    if not opened:
        driver.switch_to.default_content()
        return False
    log("DEBUG", f"Opened Queue dropdown (was {opened.strip()[:40]})")
    time.sleep(0.7)
    for _ in iter_frames(driver):
        try:
            hit = driver.execute_script(js_pick, queue_name)
            if hit:
                accept_alerts(driver)
                driver.switch_to.default_content()
                log("INFO", f"Clicked queue list item {hit}")
                return True
        except Exception:
            continue
    driver.switch_to.default_content()
    return select_by_text(driver, queue_name)


def ensure_pending_deletion_queue(driver, wait: WebDriverWait, queue_name: str) -> bool:
    shown = queue_dropdown_text(driver) or "(unknown)"
    if queue_filter_selected(driver, queue_name):
        return True
    log("INFO", f"Select queue {queue_name} (currently {shown})")
    ok = select_jqx_queue(driver, queue_name)
    time.sleep(0.8)
    shown = queue_dropdown_text(driver) or "(unknown)"
    if queue_filter_selected(driver, queue_name):
        log("INFO", f"Queue filter set to {shown}")
        return True
    log("WARN", f"Still on queue '{shown}', wanted {queue_name} (select_ok={ok})")
    return False


def open_received_pending_queue(driver, wait: WebDriverWait, queue_name: str) -> bool:
    log("INFO", "Open Queues tab")
    click_nav(
        driver,
        wait,
        ("APPNAV_QUEUES", "APPNAV_QUEUE", "APPNAV_QUEUEMGMT"),
        ("Queues",),
    )
    time.sleep(2)
    accept_alerts(driver)
    log_queue_toolbar(driver)
    shown = queue_dropdown_text(driver) or "(unknown)"
    log("INFO", f"Queue dropdown now: {shown}")
    ok = False
    for attempt in range(3):
        if ensure_pending_deletion_queue(driver, wait, queue_name):
            ok = True
            break
        time.sleep(0.5)
    select_native_option(driver, "All Users")
    time.sleep(1)
    dump_shot(driver, "queues")
    return ok


def click_refresh(driver) -> bool:
    """Click <li id=QUEUES_REFRESH title='Refresh Task List'> — not a random parent menu."""
    js = """
    function fireClick(el) {
      if (!el) return false;
      el.scrollIntoView({block: 'center', inline: 'nearest'});
      ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach((type) => {
        el.dispatchEvent(new MouseEvent(type, {
          bubbles: true, cancelable: true, view: window, buttons: 1
        }));
      });
      if (window.jQuery) jQuery(el).trigger('click');
      return true;
    }
    const el = document.getElementById('QUEUES_REFRESH')
      || document.querySelector('li[title="Refresh Task List"]')
      || (document.querySelector('li.jqx-menu-item-top .fa-refresh')
            && document.querySelector('li.jqx-menu-item-top .fa-refresh').closest('li'));
    if (el && fireClick(el)) return 'QUEUES_REFRESH';
    return '';
    """
    for _ in iter_frames(driver):
        try:
            hit = driver.execute_script(js) or ""
            if hit:
                accept_alerts(driver)
                driver.switch_to.default_content()
                log("INFO", "Clicked #QUEUES_REFRESH (Refresh Task List)")
                return True
        except Exception:
            continue
        try:
            els = driver.find_elements(By.ID, "QUEUES_REFRESH")
            if not els:
                els = driver.find_elements(By.CSS_SELECTOR, 'li[title="Refresh Task List"]')
            for el in els:
                if not el.is_displayed():
                    continue
                try:
                    el.click()
                except Exception:
                    driver.execute_script(
                        "arguments[0].dispatchEvent(new MouseEvent('click',{bubbles:true}));",
                        el,
                    )
                accept_alerts(driver)
                driver.switch_to.default_content()
                log("INFO", "Clicked #QUEUES_REFRESH (selenium)")
                return True
        except Exception:
            continue
    driver.switch_to.default_content()
    log("WARN", "QUEUES_REFRESH not found — grid not refreshed")
    return False


def scroll_queue_grid(driver) -> None:
    js = """
    const panes = document.querySelectorAll('.jqx-grid-content, .jqx-widget-content');
    panes.forEach((p) => { p.scrollTop = p.scrollHeight; });
    """
    for _ in iter_frames(driver):
        try:
            driver.execute_script(js)
        except Exception:
            continue
    driver.switch_to.default_content()


def scan_queue(driver, users: list[str], needles: list[str]) -> dict[str, bool]:
    accept_alerts(driver)
    out = {u: False for u in users}
    samples_logged = False
    for _ in iter_frames(driver):
        payload = {}
        try:
            payload = driver.execute_script(SCAN_ROWS_JS, users, needles) or {}
        except Exception as exc:
            log("DEBUG", f"Queue scan JS failed: {exc}")
        if isinstance(payload, dict) and "found" in payload:
            found = payload.get("found") or {}
            samples = payload.get("samples") or []
            ncells = payload.get("cells")
            if samples and not samples_logged:
                log("INFO", f"Grid cells={ncells} sample: {samples[0][:120]}")
                samples_logged = True
        else:
            found = payload if isinstance(payload, dict) else {}
        for u in users:
            if found.get(u):
                out[u] = True
        if all(out.values()):
            break
        try:
            text = driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            text = ""
        for u in users:
            if out[u]:
                continue
            low = text.lower()
            idx = low.find(u.lower())
            if idx < 0:
                continue
            window = text[max(0, idx - 240) : idx + 240]
            if any(n in window for n in needles):
                out[u] = True
        if all(out.values()):
            break
    driver.switch_to.default_content()
    return out


def status_line(found: dict[str, bool], users: list[tuple[str, str]]) -> str:
    parts = []
    for user, _imei in users:
        parts.append(f"{user}={'PASS' if found.get(user) else '...'}")
    return " ".join(parts)


def wait_received_queue(
    driver,
    wait: WebDriverWait,
    users: list[tuple[str, str]],
    queue_name: str,
    wait_sec: int,
    refresh_sec: int,
    monitor_day: datetime,
) -> dict[str, bool]:
    names = [u for u, _ in users]
    needles = date_needles(monitor_day)
    log("INFO", f"PASS requires Users {', '.join(names)} on {needles[0]} in {queue_name} only (not Received)")
    on_queue = open_received_pending_queue(driver, wait, queue_name)

    deadline = time.time() + max(wait_sec, 1)
    found = {u: False for u in names}

    def poll_once(elapsed_label: str) -> dict[str, bool]:
        nonlocal on_queue
        if not queue_filter_selected(driver, queue_name):
            on_queue = ensure_pending_deletion_queue(driver, wait, queue_name)
        if not queue_filter_selected(driver, queue_name):
            shown = queue_dropdown_text(driver) or "(unknown)"
            log("WARN", f"{elapsed_label}: still on '{shown}' — not scanning Received for PASS")
            return {u: False for u in names}
        click_refresh(driver)
        time.sleep(2)
        scroll_queue_grid(driver)
        time.sleep(0.4)
        got = scan_queue(driver, names, needles)
        log("INFO", f"{elapsed_label}: {status_line(got, users)}")
        return got

    found = poll_once(f"Queue check 0s/{wait_sec}s")
    if all(found.get(u) for u in names):
        return found

    while time.time() < deadline:
        remaining = int(deadline - time.time())
        sleep_for = min(refresh_sec, max(0, remaining))
        if sleep_for <= 0:
            break
        log("INFO", f"Waiting for {queue_name} ({remaining}s left): {status_line(found, users)}")
        time.sleep(sleep_for)
        if time.time() >= deadline:
            break
        elapsed = wait_sec - max(0, int(deadline - time.time()))
        found = poll_once(f"Queue check {elapsed}s/{wait_sec}s")
        if all(found.get(u) for u in names):
            return found

    found = scan_queue(driver, names, needles) if queue_filter_selected(driver, queue_name) else found
    dump_shot(driver, "queues_final")
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", default="fax.conf")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--status-file", default=STATUS_FILE)
    args = parser.parse_args()

    cfg = load_conf(args.conf)
    user = cfg.get("username") or cfg.get("email") or ""
    password = cfg.get("password") or ""
    url = cfg.get("url") or FAX_URL
    numbers = parse_numbers(cfg.get("fax_numbers") or "")
    attach = cfg.get("attach") or "scripts/fax_message.txt"
    headed = args.headed or cfg.get("headless", "1") in ("0", "false", "no")
    login_wait = int(cfg.get("login_wait_sec") or "15")
    queue_wait = int(cfg.get("queue_wait_sec") or "420")
    refresh_sec = int(cfg.get("queue_refresh_sec") or "30")
    queue_name = cfg.get("queue_name") or "ReceivedPendingDeletion"
    queue_users = parse_queue_users(cfg.get("fax_users") or "")
    status_path = cfg.get("status_file") or args.status_file
    monitor_day = datetime.now()
    found = {u: False for u, _ in queue_users}

    if not user or not password:
        log("ERROR", f"Fill username= and password= in {args.conf}")
        write_status(status_path, found, queue_users)
        return 2

    log("INFO", f"cwd={os.getcwd()}")
    attach_abs = write_fax_txt(attach)
    if not os.path.isfile(attach_abs):
        log("ERROR", f"Attachment missing: {attach_abs}")
        write_status(status_path, found, queue_users)
        return 2

    log("INFO", f"Opening Faxback {url}  headless={not headed}")
    log("INFO", f"Recipients: {', '.join(numbers)}")
    driver = build_driver(headed)
    wait = WebDriverWait(driver, 30)
    try:
        driver.get(url)
        log("INFO", f"Login page URL={driver.current_url}")
        wait.until(EC.presence_of_element_located((By.ID, "LOGIN_USERNAME")))
        user_el = driver.find_element(By.ID, "LOGIN_USERNAME")
        if not (user_el.get_attribute("value") or "").strip():
            user_el.send_keys(user)
        pass_el = driver.find_element(By.ID, "LOGIN_PASSWORD")
        pass_el.clear()
        pass_el.send_keys(password)
        driver.find_element(By.ID, "LOGIN_OK").click()
        log("INFO", f"Credentials submitted — waiting {login_wait}s")
        time.sleep(login_wait)
        accept_alerts(driver)
        dump_shot(driver, "after_login")

        wait.until(EC.presence_of_element_located((By.ID, "APPNAV")))
        log("INFO", "Click Send Fax menu")
        click_id(driver, wait, "APPNAV_SENDFAX")
        time.sleep(2)

        for fax_number in numbers:
            log("INFO", f"Add recipient {fax_number}")
            fax_input = wait.until(EC.element_to_be_clickable((By.ID, "SENDFAX_FAXNUMBER")))
            fax_input.clear()
            fax_input.send_keys(fax_number)
            click_id(driver, wait, "SENDFAX_MOV_RECIP")
            time.sleep(2)
            if not page_has(driver, fax_number[-6:]):
                log("DEBUG", f"Recipient {fax_number} not visible in page text yet")

        log("INFO", "Add attachment")
        click_id(driver, wait, "SENDFAX_ADD_ATTACH")
        time.sleep(3)
        file_input = find_file_input(driver)
        if file_input is None:
            dump_shot(driver, "no_file_input")
            log("ERROR", "No file input found after Add Attachment")
            write_status(status_path, found, queue_users)
            return 1
        driver.execute_script(
            "arguments[0].style.display='block'; arguments[0].style.visibility='visible';"
            "arguments[0].style.opacity=1; arguments[0].removeAttribute('hidden');",
            file_input,
        )
        file_input.send_keys(attach_abs)
        driver.switch_to.default_content()
        if wait_attachment_listed(driver, attach_abs):
            log("PASSED", f"Attached {attach_abs}")
        time.sleep(3)

        click_id(driver, wait, "SENDFAX_SENDFAX")
        accept_alerts(driver)
        log("INFO", f"Send Fax clicked — polling {queue_name} for {queue_wait}s (refresh every {refresh_sec}s)")

        found = wait_received_queue(
            driver, wait, queue_users, queue_name, queue_wait, refresh_sec, monitor_day
        )
        write_status(status_path, found, queue_users)
        missing = [u for u, _ in queue_users if not found.get(u)]
        if missing:
            log("FAILED", f"Fax queue FAIL after {queue_wait}s — missing {', '.join(missing)}")
            return 1
        log("PASSED", "Both fax stations received today (simplifivn1 + simplifivn2)")
        return 0
    except Exception as exc:
        log("ERROR", f"{type(exc).__name__}: {exc}")
        try:
            dump_shot(driver, "fail")
        except Exception:
            pass
        write_status(status_path, found, queue_users)
        return 1
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        shutil.rmtree(CHROME_PROFILE, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
