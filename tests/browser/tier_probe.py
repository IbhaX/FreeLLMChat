"""Entitlement probe: hits gated surfaces with a user token, prints PASS/FAIL
against the expected matrix for the account's plan.
Usage: E2E_TOKEN=... EXPECT=free|basic|plus|pro|ultra python tier_probe.py"""
import json, os, sys, urllib.request

BASE = "http://localhost:5000"
TOK = os.environ["E2E_TOKEN"]
EXPECT = os.environ.get("EXPECT", "free")

def req(method, path, body=None):
    r = urllib.request.Request(BASE + path, method=method,
        headers={"X-User-Token": TOK, "Content-Type": "application/json"})
    if body is not None:
        r.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b"{}")
        except Exception: return e.code, {}

def _err(b):
    d = b.get("detail")
    if isinstance(d, dict):
        return d.get("error", {}).get("type", "") or str(d)[:60]
    return str(d)[:60] if d else ""

results = []
def check(name, got_status, allowed, detail=""):
    ok = (got_status < 400) if allowed else (got_status == 403)
    results.append((name, got_status, allowed, ok, detail))
    print(("PASS" if ok else "FAIL"), name,
          f"[{got_status}] expected={'allow' if allowed else '403'}", detail[:80])

# entitlements snapshot
s, ent = req("GET", "/api/entitlements")
plan = ent.get("plan", "?")
caps = ent.get("capabilities", {})
limits = ent.get("limits", {})
print(f"=== plan={plan} expect={EXPECT} caps={caps}")
print(f"    limits={limits}")
check("plan matches", 200 if plan == EXPECT else 999, True, f"plan={plan}")
def cap(c): return bool(caps.get(c))

# knowledge router gate (library_captures)
s, b = req("GET", "/v1/knowledge/collections")
check("library:collections list", s, cap("library_captures"),
      _err(b) if s >= 400 else "ok")

# deep research gate — POST must not create a run for entitled check only;
# use depth=deep: entitled → starts run (real feature test), else 403.
s, b = req("POST", "/v1/research", {"message": "e2e deep probe", "depth": "deep", "save": False})
check("deep_research:run", s, cap("deep_research"),
      _err(b) if s >= 400 else f"run={b.get('id', b)}")

# memory explorer gate
s, b = req("GET", "/admin/memory/graph")
check("memory_explorer:graph", s, cap("memory_explorer"),
      _err(b) if s >= 400 else "graph ok")

# custom skills gate
s, b = req("POST", "/v1/skills/manage",
           {"name": f"e2e_{EXPECT}", "description": "e2e", "instructions": "be brief"})
check("custom_agents:skill create", s, cap("custom_agents"),
      _err(b) if s >= 400 else f"skill={b.get('id', b)}")

# negative spot-checks: lower-tier caps must stay false
for cname, minplan in (("deep_research", "plus"), ("custom_agents", "pro"),
                       ("ultra_rate_limits", "ultra")):
    order = ["free","basic","plus","pro","ultra"]
    if order.index(plan) < order.index(minplan) and cap(cname):
        results.append((f"cap {cname} leaked", 200, False, False, "granted"))
        print("FAIL", cname, "granted below", minplan)

fails = [r for r in results if not r[3]]
print(f"=== {len(results)-len(fails)}/{len(results)} pass, {len(fails)} fail")
sys.exit(1 if fails else 0)
