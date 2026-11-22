"""Drive Razorpay test checkout for a subscription.
Usage: E2E_TOKEN=... E2E_CARD=5555555555554444 E2E_PLAN_IDX=0 python rzp_drive.py
E2E_METHOD=card|upi"""
import os, sys, time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5000"
CARD = os.environ.get("E2E_CARD", "5555555555554444")
METHOD = os.environ.get("E2E_METHOD", "card")
PLAN_IDX = int(os.environ.get("E2E_PLAN_IDX", "0"))

def rzp_frame(page):
    for f in page.frames:
        if "razorpay" in f.url and "checkout" in f.url:
            return f
    return None

def dump(page, tag):
    page.screenshot(path=f"rzp_{tag}.png")
    f = rzp_frame(page)
    if f:
        try:
            txt = f.locator("body").inner_text(timeout=2500)
            print(f"--- {tag} ---")
            print(txt[:900].encode("ascii","replace").decode())
        except Exception as e:
            print(tag, "txterr", e)

def click_if(f, sels, tag, timeout=4000):
    for sel in sels:
        try:
            loc = f.locator(sel).first
            if loc.count() and loc.is_visible(timeout=timeout):
                loc.click(); print("clicked:", sel)
                return True
        except Exception:
            pass
    print("no click:", tag)
    return False

def type_into(f, sel, text):
    loc = f.locator(sel).first
    loc.click(); loc.press_sequentially(text, delay=30)
    print("typed:", sel[:40])

def pay_card(page):
    f = rzp_frame(page)
    type_into(f, "input[name='card.number']", CARD)
    type_into(f, "input[name='card.expiry']", "1230")
    type_into(f, "input[name='card.cvv']", "123")
    try:
        type_into(f, "input[name='card.name']", "E2E Tester")
    except Exception:
        pass
    time.sleep(1)
    click_if(f, ["button:has-text('Continue')", "button:has-text('Pay')",
                 "button[type='submit']"], "pay")

def pay_upi(page):
    f = rzp_frame(page)
    type_into(f, "input[name='upi'], input[placeholder*='UPI' i], input[placeholder*='VPA' i]", "success@razorpay")
    time.sleep(1)
    click_if(f, ["button:has-text('Pay')", "button:has-text('Verify')",
                 "button[type='submit']"], "upi-pay")

def drive(page):
    f = rzp_frame(page)
    type_into(f, "input[name='email']", "albi@ibhax.com")
    type_into(f, "input[type='tel']", "6379056811")
    click_if(f, ["button:has-text('Continue')"], "continue")
    time.sleep(2.5)
    f = rzp_frame(page)
    if METHOD == "upi":
        click_if(f, ["text=UPI", "[data-method='upi']"], "upi")
        time.sleep(2.5)
        f = rzp_frame(page)
        click_if(f, ["text=Enter UPI ID", "text=Pay via UPI ID",
                     "text=UPI ID"], "upi-id-opt")
        time.sleep(2)
        pay_upi(page)
    else:
        click_if(f, ["text=Cards", "[data-method='card']"], "cards")
        time.sleep(2.5)
        f = rzp_frame(page)
        pay_card(page)
    for i in range(10):
        time.sleep(4)
        f = rzp_frame(page)
        dump(page, f"s5_{i}")
        if click_if(f, ["button:has-text('Success')",
                        "button:has-text('success')",
                        "#redesign-v2-cta"], "success", timeout=2000):
            break
        if f and click_if(f, ["button:has-text('Yes, secure my card')",
                              "text=Yes, secure my card"], "secure", timeout=2000):
            continue
        # OTP step: any 4-10 digit OTP succeeds in test mode
        if f:
            otp_done = False
            for sel in ["input[name*='otp' i]", "input[placeholder*='OTP' i]",
                        "input[autocomplete='one-time-code']",
                        "input[type='tel']"]:
                try:
                    loc = f.locator(sel).first
                    if loc.count() and loc.is_visible(timeout=1200) and not (loc.input_value() or ""):
                        loc.click(); loc.press_sequentially("123456", delay=40)
                        print("otp typed:", sel); otp_done = True; break
                except Exception:
                    pass
            if otp_done:
                click_if(f, ["button:has-text('Continue')"], "otp-continue")
                continue
        # mock bank page may live in any razorpay frame
        done = False
        for fr in page.frames:
            if "razorpay" not in fr.url:
                continue
            try:
                loc = fr.locator("button:has-text('Success')").first
                if loc.count() and loc.is_visible(timeout=1200):
                    loc.click(); print("bank-success:", fr.url[:60]); done = True; break
            except Exception:
                pass
        if done:
            break
        try:
            if f and "could not be completed" in f.locator("body").inner_text(timeout=1200):
                print("GATEWAY-DECLINED"); break
        except Exception:
            pass
    time.sleep(5)
    dump(page, "s6_done")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=["--window-size=1200,900"])
    ctx = browser.new_context(viewport={"width":1280,"height":860})
    tok = os.environ["E2E_TOKEN"]
    ctx.add_init_script(f"localStorage.setItem('nova_user_token','{tok}')")
    page = ctx.new_page()
    page.goto(f"{BASE}/pay")
    page.wait_for_selector(".plan button.ui-btn-primary", timeout=15000)
    page.locator(".plan button.ui-btn-primary").nth(PLAN_IDX).click()
    time.sleep(5)
    drive(page)
    time.sleep(4)
    try:
        print("STATE:", page.locator("#payStateText").inner_text().encode("ascii","replace").decode())
        print("REF:", page.locator("#payRef").inner_text().encode("ascii","replace").decode())
    except Exception as e:
        print("state err", e)
    page.screenshot(path="rzp_final.png")
    browser.close()
