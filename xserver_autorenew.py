# -*- coding: utf-8 -*-
import os
import re
import sys
import time
from pathlib import Path
from typing import List
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

from playwright.sync_api import sync_playwright

# ------------------ Config via ENV ------------------
LOGIN_URL = "https://secure.xserver.ne.jp/xapanel/login/xserver/?request_page=xmgame%2Findex"
GAME_INDEX_URL = "https://secure.xserver.ne.jp/xapanel/xmgame/index"

HEADLESS = os.getenv("HEADLESS", "1") != "0"
EMAIL = os.getenv("XSERVER_EMAIL", "").strip()
PASSWORD = os.getenv("XSERVER_PASSWORD", "").strip()
COOKIE_STR = os.getenv("XSERVER_COOKIE", "").strip()
TARGET_GAME = os.getenv("TARGET_GAME", "").strip()

# 页面上选择的续期时长（与运行间隔无关）
RENEW_HOURS = int(os.getenv("RENEW_HOURS", "72"))

# 续期间隔限流（默认 60h；FORCE_RENEW=1 可忽略限流）
RENEW_INTERVAL_HOURS = int(os.getenv("RENEW_INTERVAL_HOURS", "60"))
FORCE_RENEW = os.getenv("FORCE_RENEW", "0") == "1"

# 写入日志 .md 的文件名与时区
RENEW_LOG_MD = os.getenv("RENEW_LOG_MD", "renew_result.md")
LOG_TIMEZONE = os.getenv("LOG_TIMEZONE", "Asia/Tokyo")

DEFAULT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "15000"))
SHORT_TIMEOUT = 4000

# ------------------ Utilities ------------------
def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def snap(page, name: str):
    try:
        out = Path("screenshots")
        ensure_dir(out)
        safe_name = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", name)
        filepath = out / f"{int(time.time())}_{safe_name}.png"
        page.screenshot(path=str(filepath), full_page=True)
        log(f"Saved screenshot: {filepath}")
    except Exception as e:
        log(f"Screenshot failed: {e}")

def dump_html(page, name: str):
    try:
        out = Path("pages")
        ensure_dir(out)
        safe = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", name)
        p = out / f"{int(time.time())}_{safe}.html"
        p.write_text(page.content(), encoding="utf-8")
        log(f"Saved page html: {p}")
    except Exception as e:
        log(f"Dump html failed: {e}")

def parse_cookie_string(cookie_str: str, domain: str) -> List[dict]:
    cookies = []
    for item in [p.strip() for p in cookie_str.split(";") if p.strip()]:
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value:
            continue
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax"
        })
    return cookies

def is_logged_in(page) -> bool:
    candidates = ["ログアウト", "マイページ", "アカウント", "お知らせ"]
    for t in candidates:
        try:
            if page.get_by_text(t, exact=False).first.is_visible():
                return True
        except Exception:
            pass
    return False

def try_click(page_or_frame, locator, timeout=SHORT_TIMEOUT) -> bool:
    try:
        locator.first.click(timeout=timeout)
        page_or_frame.wait_for_timeout(250)
        return True
    except Exception:
        return False

def click_by_text(page, texts: List[str], roles=("button", "link"), timeout=SHORT_TIMEOUT) -> bool:
    for t in texts:
        for r in roles:
            try:
                if try_click(page, page.get_by_role(r, name=t, exact=False), timeout=timeout):
                    return True
            except Exception:
                pass
        try:
            if try_click(page, page.get_by_text(t, exact=False), timeout=timeout):
                return True
        except Exception:
            pass
        for sel in [f'a:has-text("{t}")', f'button:has-text("{t}")', f'input[value*="{t}"]', f'label:has-text("{t}")']:
            try:
                if try_click(page, page.locator(sel), timeout=timeout):
                    return True
            except Exception:
                pass
    return False

def click_text_global(page, texts):
    if click_by_text(page, texts):
        return True
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        try:
            for t in texts:
                try:
                    if try_click(fr, fr.get_by_role("button", name=t, exact=False)):
                        return True
                except Exception:
                    pass
                try:
                    if try_click(fr, fr.get_by_text(t, exact=False)):
                        return True
                except Exception:
                    pass
                for sel in [f'a:has-text("{t}")', f'button:has-text("{t}")', f'input[value*="{t}"]', f'label:has-text("{t}")']:
                    try:
                        if try_click(fr, fr.locator(sel)):
                            return True
                    except Exception:
                        pass
        except Exception:
            pass
    return False

def goto(page, url: str):
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(500)

def scroll_to_bottom(page):
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        pass
    page.wait_for_timeout(400)

def accept_required_checks(page):
    # 尽量勾选“同意/確認/承諾”等复选框，避免提交按钮被禁用
    keywords = ["同意", "確認", "承諾", "同意します", "確認しました", "規約", "注意事項"]
    for k in keywords:
        try:
            page.locator(f'label:has-text("{k}")').first.click(timeout=800)
        except Exception:
            pass
    try:
        boxes = page.locator('input[type="checkbox"]')
        count = min(boxes.count(), 5)
        clicked = 0
        for i in range(count):
            el = boxes.nth(i)
            try:
                if el.is_visible() and not el.is_checked():
                    el.check(timeout=800)
                    clicked += 1
            except Exception:
                pass
        if clicked:
            log(f"Checked {clicked} agreement checkbox(es).")
    except Exception:
        pass

def click_submit_fallback(page):
    selectors = [
        'button[type="submit"]:not([disabled])',
        'input[type="submit"]:not([disabled])',
        'button:not([disabled]).is-primary, button:not([disabled]).btn-primary, button:not([disabled]).c-btn--primary',
        'a.button--primary, a.btn-primary'
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return try_click(page, loc.first, timeout=1500)
        except 
