"""Razorpay checkout iframe automation spike."""
import json, sys, time
import requests
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5000"

# register fresh user, get token
ref = open(".env").read()
rc = [l.split("=",1)[1].strip() for l in ref.splitlines() if l.startswith("REFERRAL_CODE=")][0]
r = requests.post(f"{BASE}/v1/auth/register", json={
    "username": "e2e_spike", "password": "TestPass123!",
    "email": "e2e_spike@test.local", "display_name": "E2E Spike",
    "referral_code": rc})
tok = r.json()["token"]
print("user:", r.json()["user"]["username"], "plan:", r.json()["user"].get("plan"))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=["--window-size=1200,900"])
    ctx = browser.new_context(viewport={"width":1280,"height":860})
    ctx.add_init_script(f"localStorage.setItem('nova_user_token','{tok}')")
    page = ctx.new_page()
    page.goto(f"{BASE}/pay")
    page.wait_for_selector(".plan button.ui-btn-primary", timeout=15000)
    # click Basic (first Subscribe button)
    page.locator(".plan button.ui-btn-primary").first.click()
    time.sleep(4)
    page.screenshot(path="rzp_modal.png")
    print("frames:", [f.url[:80] for f in page.frames])
    # dump iframe text content
    for f in page.frames:
        if "razorpay" in f.url or "api.razorpay" in f.url:
            try:
                txt = f.locator("body").inner_text(timeout=3000)
                print("IFRAME TEXT:\n", txt[:3000])
            except Exception as e:
                print("frame read err:", e)
            try:
                inputs = f.locator("input")
                for i in range(min(inputs.count(), 12)):
                    el = inputs.nth(i)
                    print("input:", el.get_attribute("name"), el.get_attribute("type"), el.get_attribute("placeholder"))
            except Exception as e:
                print("inputs err:", e)
    browser.close()
