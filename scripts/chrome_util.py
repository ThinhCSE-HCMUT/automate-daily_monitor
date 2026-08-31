"""Chrome/Selenium helpers that work on Raspberry Pi (Chromium) and Windows."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


def temp_profile(prefix: str) -> str:
    old = os.path.join(tempfile.gettempdir(), prefix)
    shutil.rmtree(old, ignore_errors=True)
    return os.path.abspath(tempfile.mkdtemp(prefix=prefix + "-"))


def chrome_binary() -> str | None:
    env = os.environ.get("CHROME_BIN") or os.environ.get("CHROME_PATH")
    if env and os.path.isfile(env):
        return env
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = (
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(local, r"Google\Chrome\Application\chrome.exe") if local else "",
        os.path.join(home, r"AppData\Local\Google\Chrome\Application\chrome.exe"),
    )
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return None


def linux_chromedriver() -> str | None:
    if sys.platform == "win32":
        return None
    for drv in ("/usr/bin/chromedriver", "/usr/lib/chromium-browser/chromedriver"):
        if os.path.isfile(drv):
            return drv
    return None


def kill_stale_chrome() -> None:
    if sys.platform == "win32":
        return
    os.system(
        "pkill -9 -f '/tmp/simplifi-chrome' >/dev/null 2>&1; "
        "pkill -9 -f chromedriver >/dev/null 2>&1; true"
    )


def build_chrome(
    headed: bool,
    profile: str,
    download_dir: str | None = None,
) -> webdriver.Chrome:
    if sys.platform != "win32":
        os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/dev/null")

    opts = Options()
    binary = chrome_binary()
    if binary:
        opts.binary_location = binary
    if not headed:
        # Pi Chromium often hangs on --headless=new after a previous session.
        if sys.platform == "win32":
            opts.add_argument("--headless=new")
        else:
            opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-background-networking")
    if sys.platform == "win32":
        opts.add_argument("--remote-debugging-port=0")
    opts.add_argument(f"--user-data-dir={os.path.abspath(profile)}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    opts.add_experimental_option("useAutomationExtension", False)
    if download_dir:
        abs_dl = os.path.abspath(download_dir)
        os.makedirs(abs_dl, exist_ok=True)
        opts.add_experimental_option(
            "prefs",
            {
                "download.default_directory": abs_dl,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
            },
        )
    drv = linux_chromedriver()
    last: Exception | None = None
    for attempt in range(2):
        try:
            if drv:
                return webdriver.Chrome(service=Service(executable_path=drv), options=opts)
            return webdriver.Chrome(options=opts)
        except Exception as exc:
            last = exc
            kill_stale_chrome()
            if attempt == 0:
                continue
            raise
    raise last or RuntimeError("Chrome failed to start")
