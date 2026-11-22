# Subscription System — E2E Validation Report

**Date**: 2026-11-22 · **Env**: local dev, `localhost:5000`, Razorpay **TEST MODE**
**Method**: real Razorpay Standard Web Checkout via headed Playwright Chromium
(driver: `tests/browser/e2e_live.py` — login → plan select → checkout →
RBI consent → OTP → signature verify → feature tour per tier).

## 1. Plan inventory (from `app/services/payments.py` — trusted catalogue)

| Plan | USD/mo | INR/mo | Capabilities unlocked | Limits (req/day, research/day, collections, docs, rooms) |
|---|---|---|---|---|
| free | $0 | ₹0 | — (baseline app: chat, research, roleplay, agents) | 100 / 5 / 3 / 20 / 5 |
| basic | $5 | ₹415 | `library_captures` | 300 / 15 / 8 / 50 / 15 |
| plus | $10 | ₹830 | + `deep_research`, `memory_explorer` (50% rollout) | 800 / 40 / 15 / 100 / 30 |
| pro | $20 | ₹1,660 | + `custom_agents`, `faster_streaming` | 2000 / 100 / 25 / 200 / 50 |
| ultra | $50 | ₹4,150 | + `ultra_rate_limits`, `early_access` (25% rollout) | 5000 / 200 / 100 / 1000 / 200 |

## 2. Payment transactions (real Razorpay Test Mode)

| Plan | Subscription | Payment | Amount | Signature verify | Activation |
|---|---|---|---|---|---|
| Basic | `sub_U3ANN4hOwGRIaQ` | `pay_U3AO0xaGKR604O` | ₹415 | PASS (HMAC sub scheme) | PASS — `plan=basic` persisted |
| Plus | `sub_U3AZ8NwWJk6yli` | captured in checkout | ₹830 | PASS | PASS — `plan=plus` |
| Pro | `sub_U3AdXQtBCayEWv` | captured in checkout | ₹1,660 | PASS | PASS — `plan=pro` |
| Ultra | `sub_U3AlwSezdJk1vp` | `pay_U3AsfcGGX4UzDM` | ₹4,150 | PASS | PASS — `plan=ultra` |

Test method: card `4718 6091 0820 4366` (Razorpay-documented recurring-eligible
test Visa), RBI tokenization consent, mock-bank OTP `123456`.
`4111…` and `5555…` were correctly rejected upstream ("not eligible for
recurring" / "international cards not supported") — negative path verified.

## 3. Per-tier results

### FREE (account `e2e_free`, uid 21) — PASS
- `[PASS]` plan=free, all paid caps false, free limits
- `[PASS]` /library → upgrade wall; /memory → upgrade wall; nav 🔒 badges
- `[PASS]` `POST /v1/research2/runs` depth=deep → 403 `upgrade_required`
- `[PASS]` `POST /v1/skills/manage` → 403 `upgrade_required`
- `[PASS]` positives: `/v1/models`, `/v1/roleplay/rooms` → 200; `/pay` marks Free current

### BASIC (account `e2e_spike`, uid 20) — PASS
- `[PASS]` payment `pay_U3AO0xaGKR604O` → `plan=basic` (persisted, entitlements + `/v1/auth/me`)
- `[PASS]` /library unlocked; nav 🔒 lifted on Library only
- `[PASS]` collection limit: 8×201 then **429** `collection limit reached (8/8)`
- `[PASS]` room limit: 15×201 then **429** `room limit reached (15/15)`
- `[PASS]` deep research → 403; memory graph → 403; skill create → 403

### PLUS — PASS
- `[PASS]` payment → `plan=plus`
- `[PASS]` /memory unlocked (uid 20 inside the 50% `memory_explorer` rollout — deterministic)
- `[PASS]` deep research run → HTTP 201 (run created, cancelled after)
- `[PASS]` custom skill → still 403
- `[PASS]` nav memory 🔒 lifted

### PRO — PASS
- `[PASS]` payment `sub_U3AdXQtBCayEWv` active → `plan=pro`
- `[PASS]` custom skill create → 201
- `[PASS]` `faster_streaming` cap true

### ULTRA — PASS
- `[PASS]` payment `pay_U3AsfcGGX4UzDM` → `plan=ultra`
- `[PASS]` caps: all true except `early_access` — uid 20 is outside the 25% rollout slice (expected, deterministic)
- `[PASS]` deep research 201; skill create 201
- `[PASS]` plan persists after page reload AND re-login; `/pay` marks Ultra current

## 4. Defects found & fixed during validation

| Sev | Defect | Root cause | Fix | Regression |
|---|---|---|---|---|
| **High** | `POST /api/verify-payment` → 500 after real payment; plan never granted | `get_session_factory()` (an `async_sessionmaker`) used directly as async CM in `payment_routes.py` | call it: `get_session_factory()()` | `test_verify_subscription_grants_plan_to_signed_in_user` |
| **Med** | Nav 🔒 badges missing intermittently | one-shot `querySelectorAll` at fetch-resolve raced `initTopnav`/menu mount | `_caps` cache + `applyCapLocks()` re-applied on topnav, menu, and sidebar mount | verified in later tours |
| **Low** | Sidebar lacked `data-cap`/`data-admin` lock badges | template gap | added `data-cap` + `🔒` to `admin-shell.js` navitems | visual |

## 5. Test artifacts / notes

- **Orphan subscription** `sub_U3AIX9BDIlP7lK` (basic, active upstream): first browser payment succeeded before the verify-500 fix, so it never verified in-app. Second basic mandate `sub_U3ANN4hOwGRIaQ` is the real one. Recommend cancelling the orphan on the Razorpay test dashboard.
- Several `created` (never paid) test subs exist upstream from automation attempts — harmless.
- `early_access` rollout exclusion for uid 20 is by design (deterministic 25% slice) — feature not yet wired to behavior beyond the capability flag.
- `priority_failover`/`faster_streaming` are exposed as capabilities but do not alter runtime provider ordering/stream behavior (documented soft spot).
- No payment-history/subscription-ledger model exists locally; `user.plan` is the single source of truth + upstream Razorpay records. Webhook reconciliation (`subscription.charged`, `subscription.halted`) remains a recommended follow-up.
- Console errors during runs are expected 403s from gated probes + a benign Google GSI origin warning.

## 6. Summary

| Plan | Payment | Activation | Features | Restrictions | Overall |
|---|---|---|---|---|---|
| Free | n/a | n/a | PASS | PASS | **PASS** |
| Basic | PASS | PASS | PASS | PASS | **PASS** |
| Plus | PASS | PASS | PASS | PASS | **PASS** |
| Pro | PASS | PASS | PASS | PASS | **PASS** |
| Ultra | PASS | PASS | PASS | PASS | **PASS** |

All five tiers purchased/validated through real Razorpay Test Mode checkout in
a live headed browser; entitlements, quota limits (429s), lock walls, nav
badges, and plan persistence verified end-to-end. Two real defects fixed.
