# -*- coding: utf-8 -*-
import os
import re
import sys
import time
from pathlib import Path
from typing import List
from datetime import datetime, timezone

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

# 页面上选择的续期时长
RENEW_HOURS = int(os.getenv("RENEW_HOURS", "72"))

# 触发续期的阈值时间 (小于这个小时数才触发续期)
RENEW_THRESHOLD_HOURS = int(os.getenv("RENEW_THRESHOLD_HOURS", "24"))

# 日志文件与时区
RENEW_LOG_MD = os.getenv("RENEW_LOG_MD", "renew_result.md")
LOG_TIMEZONE = os.getenv("LOG_TIMEZONE", "Asia/Tokyo")

# 等待（做了加速）
DEFAULT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "12000"))
SHORT_TIMEOUT = 3000

# ------------------ Utilities ------------------
def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def snap(page, name: str):
    """通用截图函数，保存在 screenshots 文件夹下"""
    try:
        out = Path("screenshots")
        ensure_dir(out)
        safe_name = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", name)
        filepath = out / f"{int(time.time())}_{safe_name}.png"
        page.screenshot(path=str(filepath), full_page=True)
        log(f"📸 Saved screenshot: {filepath}")
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
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": domain,
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax"
        })
    return cookies


def is_logged_in(page) -> bool:
    for t in ["ログアウト", "マイページ", "アカウント", "お知らせ"]:
        try:
            if page.get_by_text(t, exact=False).first.is_visible():
                return True
        except Exception:
            pass
    return False


def try_click(page_or_frame, locator, timeout=SHORT_TIMEOUT) -> bool:
    try:
        locator.first.click(timeout=timeout)
        page_or_frame.wait_for_timeout(200)
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
        for sel in [
            f'a:has-text("{t}")',
            f'button:has-text("{t}")',
            f'input[value*="{t}"]',
            f'label:has-text("{t}")',
        ]:
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
                for sel in [
                    f'a:has-text("{t}")',
                    f'button:has-text("{t}")',
                    f'input[value*="{t}"]',
                    f'label:has-text("{t}")',
                ]:
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
        page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(250)


def scroll_to_bottom(page):
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        pass
    page.wait_for_timeout(300)


def accept_required_checks(page):
    agree_keywords = ["同意", "確認", "承諾", "同意します", "確認しました", "規約", "注意事項"]

    for k in agree_keywords:
        try:
            label = page.locator(f'label:has-text("{k}")')
            if label.count() > 0 and label.first.is_visible():
                label.first.click(timeout=700)
        except Exception:
            pass

    try:
        boxes = page.locator('input[type="checkbox"]')
        count = min(boxes.count(), 10)
        clicked = 0
        for i in range(count):
            el = boxes.nth(i)
            try:
                if not el.is_visible() or el.is_checked():
                    continue
                parent_text = ""
                try:
                    parent_text = el.locator("xpath=ancestor::label[1]").inner_text(timeout=500)
                except Exception:
                    pass
                if not parent_text:
                    try:
                        el_id = el.get_attribute("id")
                        if el_id:
                            lbl = page.locator(f'label[for="{el_id}"]')
                            if lbl.count() > 0:
                                parent_text = lbl.first.inner_text(timeout=500)
                    except Exception:
                        pass
                if parent_text and any(kw in parent_text for kw in agree_keywords):
                    el.check(timeout=700)
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
        'a.button--primary, a.btn-primary',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return try_click(page, loc.first, timeout=1500)
        except Exception:
            pass
    return False


# ------------------ Logging to .md ------------------
def write_status_md(status_text: str, filepath=RENEW_LOG_MD, tzname=LOG_TIMEZONE):
    tz = None
    try:
        tz = ZoneInfo(tzname) if ZoneInfo else None
    except Exception:
        tz = None
    now = datetime.now(tz) if tz else datetime.now(timezone.utc)
    suffix = tzname if tz else "UTC"
    line = f"{now.strftime('%Y-%m-%d %H:%M:%S')} {suffix} {status_text}\n"
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(line)
    log(f"[MD Log] {line.strip()} -> {filepath}")


# ------------------ Auth ------------------
def cookie_login(context, page) -> bool:
    if not COOKIE_STR:
        return False
    domains = ["secure.xserver.ne.jp", "www.xserver.ne.jp"]
    all_cookies = []
    for d in domains:
        all_cookies.extend(parse_cookie_string(COOKIE_STR, d))
    if not all_cookies:
        return False
    try:
        context.add_cookies(all_cookies)
    except Exception as e:
        log(f"Add cookies failed: {e}")
        return False

    goto(page, GAME_INDEX_URL)
    if is_logged_in(page):
        log("Logged in via cookie (game index).")
        return True

    goto(page, LOGIN_URL)
    if is_logged_in(page):
        log("Logged in via cookie (login URL).")
        return True

    return False


def password_login(page) -> bool:
    if not EMAIL or not PASSWORD:
        return False
    goto(page, LOGIN_URL)

    filled_email = False
    for label in ["メールアドレス", "ログインID", "アカウントID", "ID", "メール"]:
        try:
            loc = page.get_by_label(label, exact=False)
            if loc.count() > 0:
                loc.first.fill(EMAIL, timeout=SHORT_TIMEOUT)
                filled_email = True
                break
        except Exception:
            pass
    if not filled_email:
        for css in [
            'input[type="email"]', 'input[name="mail"]', 'input[name="email"]',
            'input[id="mail"]', 'input[id="email"]', 'input[name="loginId"]', 
            'input[name="login_id"]', 'input[name="accountId"]', 'input[name="account_id"]',
            'input[id="loginId"]', 'input[id="login_id"]',
        ]:
            try:
                loc = page.locator(css)
                if loc.count() > 0:
                    loc.first.fill(EMAIL, timeout=SHORT_TIMEOUT)
                    filled_email = True
                    break
            except Exception:
                pass

    filled_pwd = False
    for label in ["パスワード", "Password"]:
        try:
            loc = page.get_by_label(label, exact=False)
            if loc.count() > 0:
                loc.first.fill(PASSWORD, timeout=SHORT_TIMEOUT)
                filled_pwd = True
                break
        except Exception:
            pass
    if not filled_pwd:
        for css in ['input[type="password"]', 'input[name="password"]', 'input[id="password"]']:
            try:
                loc = page.locator(css)
                if loc.count() > 0:
                    loc.first.fill(PASSWORD, timeout=SHORT_TIMEOUT)
                    filled_pwd = True
                    break
            except Exception:
                pass

    clicked = click_by_text(page, ["ログイン", "ログインする", "サインイン", "ログオン", "ログインへ"])
    if not clicked and filled_pwd:
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

    try:
        page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT)
    except Exception:
        pass
    return is_logged_in(page)


# ------------------ Navigation & Check ------------------
def navigate_to_game_management(page) -> bool:
    goto(page, GAME_INDEX_URL)

    def click_row_btn(row) -> bool:
        selectors = [
            'button:has-text("ゲーム管理")', '[role="button"]:has-text("ゲーム管理")',
            'a:has-text("ゲーム管理")', ':is(button,a,div,span)[class*="btn"]:has-text("ゲーム管理")',
            ':is(button,a,div,span):has-text("ゲーム管理")',
        ]
        for sel in selectors:
            try:
                loc = row.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return try_click(page, loc.first, timeout=1500)
            except Exception:
                pass
        return False

    if TARGET_GAME:
        try:
            row = page.locator("tbody tr").filter(has_text=TARGET_GAME)
            if row.count() > 0 and click_row_btn(row.first):
                try:
                    page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT)
                except Exception:
                    pass
                return True
        except Exception:
            pass

    for sel in [
        'tbody tr:has(button:has-text("ゲーム管理")) >> button:has-text("ゲーム管理")',
        'button:has-text("ゲーム管理")', 'a:has-text("ゲーム管理")',
    ]:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                if try_click(page, loc.first, timeout=1500):
                    try:
                        page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT)
                    except Exception:
                        pass
                    return True
        except Exception:
            pass

    try:
        rows = page.locator("tbody tr")
        cnt = rows.count()
        n = min(cnt if cnt else 0, 10)
        for i in range(n):
            if click_row_btn(rows.nth(i)):
                try:
                    page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT)
                except Exception:
                    pass
                return True
    except Exception:
        pass

    return False


def should_extend_based_on_time(page, threshold_hours: int) -> bool:
    """时间判断函数（包含截图留证）"""
    log(f"Checking if remaining time is less than {threshold_hours}h...")
    page.wait_for_timeout(1500) 
    
    try:
        body_text = page.locator("body").inner_text()
        
        # 匹配小时
        match = re.search(r'残り\s*(\d+)\s*時間', body_text)
        if match:
            hours = int(match.group(1))
            log(f"🕒 Found remaining time: {hours} hours.")
            
            # 📸 拍下当前剩余时间的画面
            snap(page, f"time_check_{hours}h_remaining")
            
            if hours < threshold_hours:
                log(f"✅ {hours}h < {threshold_hours}h. Will proceed.")
                return True
            else:
                log(f"🛑 {hours}h >= {threshold_hours}h. Time is sufficient, skipping.")
                return False
                
        # 匹配不足1小时的分钟
        match_min = re.search(r'残り\s*(\d+)\s*分', body_text)
        if match_min and "時間" not in body_text and "日" not in body_text:
            mins = int(match_min.group(1))
            log(f"🕒 Found remaining time: {mins} minutes.")
            
            # 📸 拍下剩余分钟的画面
            snap(page, f"time_check_{mins}m_remaining")
            return True
            
        # 解析失败兜底
        log("⚠️ Could not find '残りXX時間'. Defaulting to proceed.")
        snap(page, "time_check_failed_to_parse")
        return True

    except Exception as e:
        log(f"⚠️ Error parsing remaining time: {e}. Defaulting to proceed.")
        snap(page, "time_check_error")
        return True


def click_upgrade_or_extend(page) -> bool:
    if click_text_global(page, UPGRADE_TEXTS):
        return True

    try:
        if TARGET_GAME:
            loc = page.locator(f'text={TARGET_GAME}')
            if loc.count() > 0:
                container = loc.first
                parent = container.locator('xpath=ancestor::*[self::tr or contains(@class,"card") or contains(@class,"item")][1]')
                for t in DETAIL_TEXTS:
                    if try_click(page, parent.locator(f'text={t}')) or try_click(page, container.locator(f'text={t}')):
                        pass
        click_text_global(page, DETAIL_TEXTS)
    except Exception:
        pass

    try:
        page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT)
    except Exception:
        pass

    if click_text_global(page, UPGRADE_TEXTS):
        return True

    if click_text_global(page, CONTRACT_TEXTS):
        try:
            page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT)
        except Exception:
            pass
        if click_text_global(page, UPGRADE_TEXTS):
            return True

    return False

# ------------------ Extend ------------------
def select_hours(page, hours: int) -> bool:
    hours_str = str(hours)
    texts = [
        f"+{hours_str}時間延長", f"＋{hours_str}時間延長", f"{hours_str}時間延長",
        f"+{hours_str}時間", f"＋{hours_str}時間", f"{hours_str}時間", f"{hours_str} 時間",
    ]
    for t in texts:
        try:
            if try_click(page, page.get_by_label(t, exact=False)): return True
        except Exception: pass
    for t in texts:
        try:
            if try_click(page, page.get_by_role("radio", name=t, exact=False)): return True
        except Exception: pass
    for sel in [
        f'input[type="radio"][value="{hours_str}"]', f'input[type="radio"][value*="{hours_str}"]',
        f'input[value="{hours_str}"]', f'input[value*="{hours_str}"]',
    ]:
        try:
            if try_click(page, page.locator(sel)): return True
        except Exception: pass
    return click_text_global(page, texts)


def do_extend_hours(page, hours: int) -> bool:
    scroll_to_bottom(page)
    click_text_global(page, ["期限を延長する", "延長する"])
    try:
        page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT)
    except Exception:
        pass

    if not select_hours(page, hours):
        log(f"Could not select +{hours}時間 option. Aborting.")
        snap(page, f"failed_select_{hours}h")
        return False

    accept_required_checks(page)

    if not click_text_global(page, ["確認画面に進む", "確認へ進む", "確認画面へ", "確認"]):
        log("Could not find 確認画面に進む.")
    else:
        try:
            page.wait_for_load_state("load", timeout=DEFAULT_TIMEOUT)
        except Exception:
            pass

    scroll_to_bottom(page)
    accept_required_checks(page)

    final_texts = [
        "期限を延長する", "延長する", "実行する",
        "延長を確定する", "確定する",
        "申込みを確定する", "お申し込みを確定する",
        "申込を確定する", "お申込みを確定する",
    ]
    if not click_text_global(page, final_texts):
        if not click_submit_fallback(page):
            snap(page, "failed_final_extend_click")
            return False

    log("Final submit button clicked, waiting for result...")
    
    page.wait_for_timeout(2000) 
    
    error_keywords = [
        "エラー", "失敗", "不足", "できません", "無効", 
        "残高が不足", "同意してください"
    ]
    for err in error_keywords:
        try:
            err_loc = page.locator(f'text="{err}"').first
            if err_loc.is_visible(timeout=500):
                log(f"🚨 Failed: Detected error message on page: '{err}'")
                snap(page, "extend_error_detected")
                return False
        except Exception:
            pass

    success_keywords = [
        "完了", "処理が完了", "更新されました",
        "受け付けました", "受付しました", "手続きが完了",
    ]
    
    is_success = False
    for t in success_keywords:
        try:
            loc = page.locator(f'h1:has-text("{t}"), h2:has-text("{t}"), h3:has-text("{t}"), div.complete-message:has-text("{t}"), p:has-text("{t}")').first
            loc.wait_for(state="visible", timeout=10000)
            log(f"✅ Extension succeeded (Strictly detected: '{t}').")
            is_success = True
            break
        except Exception:
            try:
                fallback_loc = page.get_by_text(t, exact=False).first
                fallback_loc.wait_for(state="visible", timeout=5000)
                
                html_snippet = fallback_loc.evaluate("el => el.outerHTML", timeout=2000)
                if html_snippet and "nav" not in html_snippet.lower() and "footer" not in html_snippet.lower():
                    log(f"✅ Extension succeeded (Fallback detected: '{t}').")
                    is_success = True
                    break
            except Exception:
                pass

    if is_success:
        snap(page, "extend_success_confirmed") # 📸 续约成功的截图
        return True

    log("❌ No success message strictly detected after submission timeout.")
    snap(page, "no_success_message_timeout")
    return False


# ------------------ Main ------------------
def main():
    if not COOKIE_STR and (not EMAIL or not PASSWORD):
        log("No cookie provided and missing EMAIL/PASSWORD.")
        sys.exit(1)

    rc = 1  
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        try:
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT)

            logged_in = False
            if COOKIE_STR:
                log("Trying cookie login...")
                logged_in = cookie_login(context, page)
            if not logged_in and EMAIL and PASSWORD:
                log("Trying password login...")
                logged_in = password_login(page)

            if not logged_in:
                snap(page, "login_failed")
                log("Login failed.")
                rc = 2
                return  

            log("Navigating to Game Management...")
            if not navigate_to_game_management(page):
                log("Could not open ゲーム管理.")
                rc = 3
                return

            # ---------- 核心：时间校验拦截 ----------
            if not should_extend_based_on_time(page, RENEW_THRESHOLD_HOURS):
                # 📸 拍下跳过时的最后状态
                snap(page, "skipped_sufficient_time")
                write_status_md("跳过 (剩余时间充足)", RENEW_LOG_MD, LOG_TIMEZONE)
                rc = 0
                return

            log("Opening アップグレード・期限延長...")
            if not click_upgrade_or_extend(page):
                log("Could not open upgrade/extend page.")
                rc = 3
                return

            log(f"Performing +{RENEW_HOURS}h extension...")
            success = do_extend_hours(page, RENEW_HOURS)

            if success:
                write_status_md("成功", RENEW_LOG_MD, LOG_TIMEZONE)
                log("All steps completed successfully.")
                rc = 0
            else:
                log("Extension step reported failure.")
                rc = 4

        except Exception as e:
            log(f"Unexpected error: {e}")
            try:
                snap(page, "unexpected_error")
            except Exception:
                pass
            rc = 5

        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()

    sys.exit(rc)


if __name__ == "__main__":
    main()
