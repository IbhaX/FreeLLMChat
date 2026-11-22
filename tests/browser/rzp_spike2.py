import json, sys, time
import requests
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5000"
CARD = {"num": "4111111111111111", "exp": "12/30", "cvv": "123",
        "name": "E2E Tester"}

def rzp_frame(page):
    for f in page.frames:
        if "razorpay" in f.url and "checkout" in f.url:
            return f
    return None

def dump(page, tag):
    f = rzp_frame(page)
    if not f:
        print(tag, "no rzp frame"); return
    try:
        txt = f.locator("body").inner_text(timeout=3000)
        print(f"--- {tag} ---")
        print(txt[:1500].encode("ascii", "replace").decode())
    except Exception as e:
        print(tag, "err", e)
    page.screenshot(path=f"rzp_{tag}.png")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=["--window-size=1200,900"])
    ctx = browser.new_context(viewport={"width":1280,"height":860})
    tok = open("e2e_token.txt").read().strip() if False else None
    import os
    tok = os.environ["E2E_TOKEN"]
    ctx.add_init_script(f"localStorage.setItem('nova_user_token','{tok}')")
    page = ctx.new_page()
    page.goto(f"{BASE}/pay")
    page.wait_for_selector(".plan button.ui-btn-primary", timeout=15000)
    page.locator(".plan button.ui-btn-primary").first.click()
    time.sleep(5)
    dump(page, "s1_open")
    f = rzp_frame(page)
    # fill contact
    try:
        f.locator("input[type='tel']").fill("9999999999")
        f.locator("input[type='email']").fill("e2e@test.local")
    except Exception as e:
        print("contact fill err:", e)
    # click any visible continue/skip/proceed
    for sel in ["text=Continue", "text=Skip", "button:has-text('Continue')",
                "button[type='submit']"]:
        try:
            loc = f.locator(sel).first
            if loc.count() and loc.is_visible(timeout=1500):
                loc.click(); print("clicked", sel); break
        except Exception:
            pass
    time.sleep(3)
    dump(page, "s2_after_contact")
    browser.close()
