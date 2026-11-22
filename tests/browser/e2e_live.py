"""LIVE E2E subscription validation — visible Playwright run.

One headed browser walks every tier: real login, plan page, real Razorpay
checkout, then a tour of gated surfaces after each activation. An on-screen
overlay narrates each check; results land in .e2e/live_report.json.

Run:  python tests/browser/e2e_live.py
"""
import json
import os
import time

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5000"
SHOTS = ".e2e/shots"
RESULTS = []
CONSOLE_ERRS = []
CARD = "4718609108204366"  # Razorpay recurring-eligible test card (Visa)
EMAIL = "albi@ibhax.com"
PHONE = "6379056811"
PW = "TestPass123!"


def rec(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail[:200]})
    print(("PASS " if ok else "FAIL ") + name
          + (" — " + detail if detail else ""))


def overlay(page, text, ok=None):
    """Floating narration so the viewer can follow the checks."""
    color = "#8a97ad" if ok is None else ("#34d399" if ok else "#f87171")
    try:
        page.evaluate("""([t,c])=>{
          let o=document.getElementById('__e2e_overlay');
          if(!o){o=document.createElement('div');o.id='__e2e_overlay';
            o.style.cssText='position:fixed;left:14px;bottom:14px;z-index:999999;'
             +'max-width:520px;background:rgba(10,14,22,.92);color:#e6edf7;'
             +'border:1px solid #33405a;border-radius:10px;padding:10px 14px;'
             +'font:12px/1.5 ui-monospace,monospace;box-shadow:0 8px 30px #0008;'
             +'max-height:40vh;overflow:auto';
            document.body.appendChild(o);}
          const d=document.createElement('div');
          d.style.color=c;d.textContent=t;o.appendChild(d);
          o.scrollTop=o.scrollHeight;}""", [text, color])
    except Exception:
        pass


def step(page, text):
    print(">> " + text)
    overlay(page, "\u00bb " + text)


def shot(page, name):
    try:
        page.screenshot(path=f"{SHOTS}/{name}.png")
    except Exception as e:
        print("shot err", name, e)


def wire(page):
    page.on("console", lambda m: CONSOLE_ERRS.append(m.text)
            if m.type == "error" else None)
    page.on("pageerror", lambda e: CONSOLE_ERRS.append("PAGEERROR " + str(e)))


def rzp_frame(page):
    for f in page.frames:
        if "razorpay" in f.url and "checkout" in f.url:
            return f
    return None


def click_if(f, sels, timeout=3500):
    for sel in sels:
        try:
            loc = f.locator(sel).first
            if loc.count() and loc.is_visible(timeout=timeout):
                loc.click()
                return True
        except Exception:
            pass
    return False


def type_into(f, sel, text):
    loc = f.locator(sel).first
    loc.click()
    loc.press_sequentially(text, delay=35)


def api(page, method, path, body=None):
    """In-page fetch with the session token — real backend, real auth."""
    return page.evaluate("""async ([m,p,b])=>{
      const t=localStorage.getItem('nova_user_token');
      const r=await fetch(p,{method:m,headers:{'X-User-Token':t,
        'Content-Type':'application/json'},body:b?JSON.stringify(b):null});
      let j=null;try{j=await r.json()}catch(e){}
      return {status:r.status,body:j};}""", [method, path, body])


def login(page, user, pw):
    step(page, f"Sign in as {user}")
    page.goto(BASE + "/")
    page.evaluate("localStorage.clear()")
    page.goto(BASE + "/login")
    page.wait_for_selector("#u_name", timeout=15000)
    page.fill("#u_name", user)
    page.fill("#u_pass", pw)
    page.click("#submitBtn")
    page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
    time.sleep(1.2)


def expect_wall(page, name):
    step(page, f"{name}: expect upgrade wall")
    wall = page.locator("text=Plan upgrade required").first
    ok = wall.count() > 0 and wall.is_visible(timeout=8000)
    rec(f"{name} locked (wall)", ok)
    overlay(page, f"{chr(10004) if ok else chr(10008)} {name} -> upgrade wall",
            ok)
    return ok


def expect_unlocked(page, name):
    step(page, f"{name}: expect unlocked")
    time.sleep(2)
    wall = page.locator("text=Plan upgrade required")
    ok = wall.count() == 0
    rec(f"{name} unlocked", ok)
    overlay(page, f"{chr(10004) if ok else chr(10008)} {name} -> accessible",
            ok)
    return ok


def api_check(page, name, method, path, body, expect):
    j = api(page, method, path, body)
    st = j["status"]
    ok = (st == expect) if isinstance(expect, int) else (st in expect)
    err = ""
    if isinstance(j.get("body"), dict):
        d = j["body"].get("detail")
        err = (d.get("error", {}).get("type", "") if isinstance(d, dict)
               else str(d)[:50])
    rec(name, ok, f"HTTP {st} {err}")
    overlay(page,
            f"{chr(10004) if ok else chr(10008)} {name} -> HTTP {st} {err}",
            ok)
    return j


def checkout(page, plan_label):
    """Real Razorpay checkout for the plan card whose title matches."""
    step(page, f"Checkout: {plan_label}")
    page.goto(BASE + "/")
    if not page.evaluate("localStorage.getItem('nova_user_token')"):
        login(page, "e2e_spike", PW)
    page.goto(BASE + "/pay")
    page.wait_for_selector(".plan", timeout=15000)
    card = page.locator(".plan", has_text=plan_label).first
    shot(page, f"plan_{plan_label.lower()}")
    card.locator("button.ui-btn-primary").first.click()
    time.sleep(6)
    shot(page, f"{plan_label.lower()}_paying")
    paid = False
    contact_done = card_done = False
    for i in range(18):
        time.sleep(3)
        f = rzp_frame(page)
        if f is None:
            print(f"  [{i}] no rzp frame; frames={[fr.url[:70] for fr in page.frames]}")
            continue
        try:
            txt = f.locator("body").inner_text(timeout=1500)
        except Exception:
            txt = ""
        vis = []
        for sel in ("input[name='email']", "input[name='card.number']",
                    "input[name*='otp' i]"):
            try:
                l = f.locator(sel).first
                if l.count() and l.is_visible(timeout=400):
                    vis.append(sel.split("'")[1])
            except Exception:
                pass
        print(f"  [{i}] {txt[:80].encode('ascii','replace').decode()!r} vis={vis}")
        if "Payment Successful" in txt:
            paid = True
            break
        if "could not be completed" in txt:
            overlay(page, "gateway declined: " + txt[-120:], False)
            rec(f"{plan_label} payment", False, "gateway declined")
            return False
        # RBI tokenisation consent
        if click_if(f, ["button:has-text('Yes, secure my card')"],
                    timeout=1200):
            continue
        # OTP step — any 4-10 digit code passes in test mode
        otp_done = False
        for sel in ["input[name*='otp' i]", "input[placeholder*='OTP' i]",
                    "input[autocomplete='one-time-code']"]:
            try:
                loc = f.locator(sel).first
                if loc.count() and loc.is_visible(timeout=1200) \
                        and not (loc.input_value() or ""):
                    loc.click()
                    loc.press_sequentially("123456", delay=40)
                    otp_done = True
                    break
            except Exception:
                pass
        if otp_done:
            click_if(f, ["button:has-text('Continue')"])
            continue
        # Contact step (new customers only; returning users skip it)
        if not contact_done:
            try:
                em = f.locator("input[name='email']").first
                if em.count() and em.is_visible(timeout=1200):
                    type_into(f, "input[name='email']", EMAIL)
                    type_into(f, "input[type='tel']", PHONE)
                    click_if(f, ["button:has-text('Continue')"])
                    contact_done = True
                    continue
            except Exception:
                pass
        # Card entry step
        try:
            cn = f.locator("input[name='card.number']").first
            if cn.count() and cn.is_visible(timeout=1200):
                if not card_done or not (cn.input_value() or ""):
                    type_into(f, "input[name='card.number']", CARD)
                    type_into(f, "input[name='card.expiry']", "1230")
                    type_into(f, "input[name='card.cvv']", "123")
                    card_done = True
                click_if(f, ["button:has-text('Continue')",
                             "button:has-text('Pay')",
                             "button[type='submit']"])
                continue
        except Exception:
            pass
        # Saved-card / method list — pick Cards / a stored card
        if click_if(f, ["text=Cards", "[data-method='card']"],
                    timeout=1200):
            continue
        # Any other generic continue/pay CTA (saved card confirm etc.)
        click_if(f, ["button:has-text('Continue')"], timeout=1200)
    rec(f"{plan_label} payment", paid)
    overlay(page, "Payment Successful" if paid else "no success screen", paid)
    if not paid:
        return False
    time.sleep(6)  # redirect + verify-payment roundtrip
    try:
        state = page.locator("#payStateText").inner_text(timeout=8000)
        ok = "verified" in state.lower() or "active" in state.lower()
    except Exception:
        state, ok = "(no state)", False
    rec(f"{plan_label} backend activation", ok, state.strip()[:120])
    overlay(page, state.strip()[:90], ok)
    shot(page, f"{plan_label.lower()}_verified")
    return ok


def ensure_login(page, user="e2e_spike"):
    page.goto(BASE + "/")
    if not page.evaluate("localStorage.getItem('nova_user_token')"):
        login(page, user, PW)


def tour_common_locks(page, plan):
    """Nav lock badges + entitlements cross-check."""
    step(page, f"[{plan}] nav lock badges + entitlements")
    ensure_login(page)
    page.goto(BASE + "/dashboard")
    for _ in range(30):  # nav + entitlements resolve async
        if page.locator("a[data-cap='library']").count():
            break
        time.sleep(0.5)
    # The entitlements fetch resolves async — poll for the lock classes.
    lib_locked = mem_locked = False
    for _ in range(20):
        time.sleep(0.5)
        el = page.locator("a[data-cap='library']")
        lib_locked = (el.count() > 0
                      and "locked" in (el.first.get_attribute("class") or ""))
        mem = page.locator("a[data-cap='memory']")
        mem_locked = (mem.count() > 0
                      and "locked" in (mem.first.get_attribute("class") or ""))
        ent_caps = api(page, "GET", "/api/entitlements")["body"]["capabilities"]
        if (lib_locked == (not ent_caps["library_captures"])
                and mem_locked == (not ent_caps["memory_explorer"])):
            break
    overlay(page, f"nav: library {'LOCK' if lib_locked else 'open'} "
                  f"memory {'LOCK' if mem_locked else 'open'}")
    j = api(page, "GET", "/api/entitlements")
    ent = j["body"]
    rec(f"[{plan}] /api/entitlements plan", ent["plan"] == plan, ent["plan"])
    rec(f"[{plan}] nav library lock",
        lib_locked == (not ent["capabilities"]["library_captures"]),
        f"locked={lib_locked}")
    rec(f"[{plan}] nav memory lock",
        mem_locked == (not ent["capabilities"]["memory_explorer"]),
        f"locked={mem_locked}")
    overlay(page, f"caps: {ent['capabilities']}")
    return ent


def tour_free(page):
    step(page, "======= FREE TIER TOUR =======")
    login(page, "e2e_free", PW)
    ent = tour_common_locks(page, "free")
    rec("[free] all paid caps false",
        not any(ent["capabilities"].values()), str(ent["capabilities"]))
    page.goto(BASE + "/library")
    time.sleep(2.5)
    expect_wall(page, "library")
    shot(page, "free_library_wall")
    page.goto(BASE + "/memory")
    time.sleep(2.5)
    expect_wall(page, "memory")
    shot(page, "free_memory_wall")
    api_check(page, "[free] deep research API", "POST", "/v1/research2/runs",
              {"question": "e2e deep probe", "depth": "deep"}, 403)
    api_check(page, "[free] custom skill API", "POST", "/v1/skills/manage",
              {"name": "e2e_free_x", "description": "x", "instructions": "x"},
              403)
    api_check(page, "[free] models (positive)", "GET", "/v1/models", None, 200)
    api_check(page, "[free] rooms list (positive)", "GET",
              "/v1/roleplay/rooms", None, 200)
    page.goto(BASE + "/pay")
    time.sleep(2)
    cur = page.locator(".plan", has_text="Free").locator(".free-note")
    ok = cur.count() > 0 and "Current plan" in cur.first.inner_text()
    rec("[free] /pay marks Free current", ok)
    shot(page, "free_pay")


def tour_basic(page):
    step(page, "======= BASIC TIER TOUR =======")
    login(page, "e2e_spike", PW)
    tour_common_locks(page, "basic")
    page.goto(BASE + "/library")
    time.sleep(3)
    expect_unlocked(page, "library")
    shot(page, "basic_library")
    api_check(page, "[basic] 9th collection (limit 8)", "POST",
              "/v1/knowledge/collections", {"name": "e2e_over"}, 429)
    api_check(page, "[basic] deep research API", "POST", "/v1/research2/runs",
              {"question": "e2e deep probe", "depth": "deep"}, 403)
    page.goto(BASE + "/memory")
    time.sleep(2.5)
    expect_wall(page, "memory")
    api_check(page, "[basic] custom skill API", "POST", "/v1/skills/manage",
              {"name": "e2e_basic_x", "description": "x", "instructions": "x"},
              403)
    api_check(page, "[basic] 16th room (limit 15)", "POST",
              "/v1/roleplay/rooms", {"name": "e2e_over", "character_ids": [1]},
              429)


def tour_plus(page):
    step(page, "======= PLUS TIER TOUR =======")
    tour_common_locks(page, "plus")
    page.goto(BASE + "/memory")
    time.sleep(4)
    if expect_unlocked(page, "memory"):
        time.sleep(3)
        shot(page, "plus_memory_graph")
    j = api_check(page, "[plus] deep research run", "POST",
                  "/v1/research2/runs",
                  {"question": "e2e plus deep run", "depth": "deep"},
                  (200, 201))
    if j["status"] in (200, 201):
        rid = (j["body"] or {}).get("id", "")
        overlay(page, f"deep run started id={str(rid)[:24]}", True)
        api(page, "POST", f"/v1/research2/runs/{rid}/cancel")
    api_check(page, "[plus] custom skill API", "POST", "/v1/skills/manage",
              {"name": "e2e_plus_x", "description": "x", "instructions": "x"},
              403)


def tour_pro(page):
    step(page, "======= PRO TIER TOUR =======")
    tour_common_locks(page, "pro")
    page.goto(BASE + "/skills")
    time.sleep(2.5)
    j = api_check(page, "[pro] custom skill create", "POST",
                  "/v1/skills/manage",
                  {"name": "e2e_pro_skill", "description": "e2e",
                   "instructions": "be brief"}, 201)
    if j["status"] == 201:
        overlay(page, "custom skill created", True)
    shot(page, "pro_skills")


def tour_ultra(page):
    step(page, "======= ULTRA TIER TOUR =======")
    ent = tour_common_locks(page, "ultra")
    caps = ent["capabilities"]
    # uid 20 sits outside the early_access 25% rollout — expected exclusion.
    expected = {c: (c != "early_access") for c in caps}
    rec("[ultra] caps", all(caps.get(c) == v for c, v in expected.items()),
        str(caps))
    api_check(page, "[ultra] deep research run", "POST", "/v1/research2/runs",
              {"question": "e2e ultra deep run", "depth": "deep"}, (200, 201))
    api_check(page, "[ultra] custom skill create", "POST", "/v1/skills/manage",
              {"name": "e2e_ultra_skill", "description": "e2e",
               "instructions": "be brief"}, 201)
    page.goto(BASE + "/pay")
    page.reload()
    time.sleep(2)
    j = api(page, "GET", "/api/entitlements")
    rec("[ultra] plan persists after reload", j["body"]["plan"] == "ultra")
    login(page, "e2e_spike", PW)
    j = api(page, "GET", "/api/entitlements")
    rec("[ultra] plan persists after re-login", j["body"]["plan"] == "ultra")
    page.goto(BASE + "/pay")
    time.sleep(2)
    cur = page.locator(".plan", has_text="Ultra").locator(".free-note")
    ok = cur.count() > 0 and "Current plan" in cur.first.inner_text()
    rec("[ultra] /pay marks Ultra current", ok)
    shot(page, "ultra_pay")


def main():
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150,
                                    args=["--window-size=1400,920"])
        ctx = browser.new_context(viewport={"width": 1380, "height": 880})
        page = ctx.new_page()
        wire(page)
        try:
            phases = [
                ("FREE tour", lambda: tour_free(page)),
                ("BASIC tour", lambda: tour_basic(page)),
                ("PLUS checkout", lambda: checkout(page, "Plus")),
                ("PLUS tour", lambda: tour_plus(page)),
                ("PRO checkout", lambda: checkout(page, "Pro")),
                ("PRO tour", lambda: tour_pro(page)),
                ("ULTRA checkout", lambda: checkout(page, "Ultra")),
                ("ULTRA tour", lambda: tour_ultra(page)),
            ]
            start = os.environ.get("E2E_FROM", "").lower()
            skip_rest = False
            for name, fn in phases:
                if start:
                    if name.lower().startswith(start):
                        start = ""
                    else:
                        print(f"(skip {name})")
                        continue
                if skip_rest:
                    rec(name + " skipped", False, "earlier phase failed")
                    continue
                try:
                    ok = fn()
                except Exception as e:
                    rec(name + " crashed", False, repr(e)[:160])
                    shot(page, f"crash_{name.split()[0].lower()}")
                    ok = False
                # A failed checkout blocks later paid tiers.
                if "checkout" in name.lower() and ok is False:
                    skip_rest = True
        finally:
            fails = [r for r in RESULTS if not r["ok"]]
            summary = {"results": RESULTS, "console_errors": CONSOLE_ERRS,
                       "passed": len(RESULTS) - len(fails),
                       "failed": len(fails)}
            open(".e2e/live_report.json", "w").write(
                json.dumps(summary, indent=1))
            print(f"\n=== {summary['passed']}/{len(RESULTS)} pass, "
                  f"{len(fails)} fail, {len(CONSOLE_ERRS)} console errors")
            for f in fails:
                print("  FAIL:", f["check"], f["detail"])
            overlay(page, f"E2E done: {summary['passed']}/{len(RESULTS)} pass",
                    not fails)
            time.sleep(6)
            browser.close()


if __name__ == "__main__":
    main()
