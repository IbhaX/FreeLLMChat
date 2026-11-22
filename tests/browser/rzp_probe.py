import os, time
from playwright.sync_api import sync_playwright
BASE="http://localhost:5000"
def rzp_frame(page):
    for f in page.frames:
        if "razorpay" in f.url and "checkout" in f.url: return f
with sync_playwright() as p:
    browser=p.chromium.launch(headless=False)
    ctx=browser.new_context(viewport={"width":1280,"height":860})
    ctx.add_init_script(f"localStorage.setItem('nova_user_token','{os.environ['E2E_TOKEN']}')")
    page=ctx.new_page(); page.goto(f"{BASE}/pay")
    page.wait_for_selector(".plan button.ui-btn-primary",timeout=15000)
    page.locator(".plan button.ui-btn-primary").first.click(); time.sleep(5)
    f=rzp_frame(page)
    f.locator("input[name='email']").first.click()
    f.locator("input[name='email']").first.press_sequentially("albi@ibhax.com",delay=25)
    f.locator("input[type='tel']").first.click()
    f.locator("input[type='tel']").first.press_sequentially("6379056811",delay=25)
    time.sleep(.5); f.locator("button:has-text('Continue')").first.click(); time.sleep(3)
    f=rzp_frame(page)
    f.locator("text=Cards").first.click(); time.sleep(3)
    f=rzp_frame(page)
    ins=f.locator("input")
    print("input count:",ins.count())
    for i in range(ins.count()):
        e=ins.nth(i)
        print(i, e.get_attribute("name"), e.get_attribute("type"),
              e.get_attribute("placeholder"), e.get_attribute("id"),
              e.get_attribute("autocomplete"), e.is_visible())
    # also list iframes inside rzp frame (card fields are often nested)
    print("all frames:",[fr.url[:90] for fr in page.frames])
    page.screenshot(path="rzp_cardform.png")
    browser.close()
