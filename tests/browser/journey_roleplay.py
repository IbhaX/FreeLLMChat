"""Deterministic roleplay research journey against the isolated 5011 fixture.

FIXTURE verification only: the provider (gateway) and the search/fetch tools
are scripted by tests/browser/fixture_app.py; routes, finalization, rendering
and persistence are the real application code. Live provider/search quality
is NOT proven by this journey.

Usage (server must already run on --url):
    python -m tests.browser.journey_roleplay --url http://127.0.0.1:5011 --out DIR
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
PROMPT_TABLE = "research the baseline and alternative options for me"
PROMPT_CODE = "research a code sample for the baseline"
PROMPT_DOSSIER = "tell me the complete life history of Albin Doe"


def _post(base: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def seed(base: str) -> dict[str, Any]:
    """Create one character and a tools-enabled room in the temp fixture DB."""
    ch = _post(base, "/v1/characters", {
        "name": "Holmes", "emoji": "🔍", "greeting": "Greetings.",
        "personality": "Precise and curious."})
    room = _post(base, "/v1/roleplay/rooms", {
        "name": "Fixture Journey", "character_ids": [ch["id"]],
        "tools_enabled": True})
    return {"character": ch, "room": room}


def _wait_turn_done(page: Any) -> None:
    page.wait_for_function(
        "() => !document.querySelector('#transcript .st-msg.writing') && "
        "!document.querySelector('#transcript .cursor')", timeout=60000)


def run(base: str, out: Path) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    seeded = seed(base)
    A: dict[str, Any] = {}
    shots: dict[str, str] = {}
    console_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))
        page.goto(base + "/roleplay", wait_until="domcontentloaded")
        page.wait_for_selector("#composer textarea", timeout=20000)

        # ---- turn 1: plain markdown table answer with sources
        page.fill("#composer textarea", PROMPT_TABLE)
        page.click("#btnRun")
        page.wait_for_selector(".lr-link[href*='example.org/primary']",
                               state="visible", timeout=30000)
        A["source_visible_before_final_table"] = (
            page.locator("#transcript .nova-table").count() == 0)
        _wait_turn_done(page)
        A["comp_slot_count"] = page.locator("#transcript .nova-comp-slot").count()
        A["native_table_count"] = page.locator("#transcript .nova-table").count()
        A["native_table_body_rows"] = page.locator(
            "#transcript .nova-table tbody tr").count()
        A["search_card_count"] = page.locator(
            "#transcript .nova-search-card").count()
        A["final_source_links_example_org"] = page.locator(
            "#transcript a[href^='https://example.org/']").count()
        text1 = page.locator("#transcript").inner_text()
        A["no_raw_protocol_turn1"] = ("nova-response" not in text1
                                      and '"response_type"' not in text1)
        A["no_cursor_after_terminal"] = page.locator(
            "#transcript .cursor").count() == 0
        table_text_before_reload = page.locator(
            "#transcript .nova-table").first.inner_text()
        page.screenshot(path=str(out / "roleplay_desktop_1280.png"))
        shots["desktop"] = str(out / "roleplay_desktop_1280.png")

        # ---- turn 2: code block component
        page.fill("#composer textarea", PROMPT_CODE)
        page.click("#btnRun")
        page.wait_for_selector("#transcript .nova-codeblock", timeout=60000)
        _wait_turn_done(page)
        A["code_block_count"] = page.locator("#transcript .nova-codeblock").count()
        A["code_contains_print"] = "print('baseline')" in page.locator(
            "#transcript .nova-codeblock").first.inner_text()

        # ---- turn 3: dossier (deep research) — live findings + long report
        prior_visible = page.evaluate(
            "() => [...document.querySelectorAll('#transcript .lr-finding')]"
            ".filter(e => e.offsetParent).length")
        page.fill("#composer textarea", PROMPT_DOSSIER)
        page.click("#btnRun")
        # Live findings must appear BEFORE the final answer arrives.
        page.wait_for_function(
            f"() => [...document.querySelectorAll('#transcript .lr-finding')]"
            f".filter(e => e.offsetParent).length > {prior_visible}",
            timeout=30000)
        A["dossier_finding_visible_live"] = True
        A["dossier_final_not_yet"] = (
            page.locator("#transcript .nova-personcard").count() == 0)
        _wait_turn_done(page)
        text3 = page.locator("#transcript").inner_text()
        A["dossier_full_report_sentinel"] = "DOSSIER-SENTINEL-END" in text3
        A["dossier_gaps_section"] = (
            "not established from the available public sources" in text3)
        A["dossier_person_card"] = page.locator(
            "#transcript .nova-person").count() >= 1
        A["dossier_timeline"] = page.locator(
            "#transcript .nova-timeline").count() >= 1
        A["dossier_source_card"] = page.locator(
            "#transcript .nova-search-card").count() >= 1
        A["dossier_no_raw_protocol"] = (
            "nova-response" not in text3 and '"response_type"' not in text3)
        # Metrics dashboard routed the scripted usage event.
        dash = page.locator("#transcript .alog-dash").last
        A["dossier_calls_text"] = dash.locator(
            '[data-m="calls"] .alog-dv').inner_text()
        A["dossier_tokens_text"] = dash.locator(
            '[data-m="tokens"] .alog-dv').inner_text()
        A["dossier_usage_counted"] = (
            A["dossier_calls_text"] != "0"
            and A["dossier_tokens_text"] != "0")
        A["dossier_no_cursor"] = page.locator(
            "#transcript .cursor").count() == 0
        A["dossier_no_writing"] = page.locator(
            "#transcript .writing").count() == 0
        page.screenshot(path=str(out / "roleplay_dossier.png"))
        shots["dossier"] = str(out / "roleplay_dossier.png")

        # ---- reload: persisted response renders identically
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#transcript .nova-table", timeout=30000)
        A["reload_table_body_rows"] = page.locator(
            "#transcript .nova-table tbody tr").count()
        A["reload_table_identical"] = page.locator(
            "#transcript .nova-table").first.inner_text() == table_text_before_reload
        A["reload_search_card_count"] = page.locator(
            "#transcript .nova-search-card").count()

        # ---- mobile 390: no page-level horizontal overflow
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - "
            "document.documentElement.clientWidth")
        A["mobile_390_overflow_px"] = overflow
        page.screenshot(path=str(out / "roleplay_mobile_390.png"), full_page=True)
        shots["mobile"] = str(out / "roleplay_mobile_390.png")
        browser.close()

    app_errors = [e for e in console_errors
                  if "pageerror" in e or "Uncaught" in e
                  or "127.0.0.1:5011" in e]
    evidence = {
        "server": base,
        "fixture": True,
        "seeded_room": seeded["room"]["id"],
        "assertions": A,
        "app_console_errors": app_errors,
        "external_console_messages_ignored": len(console_errors) - len(app_errors),
        "screenshots": shots,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (out / "journey_roleplay_evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5011")
    parser.add_argument("--out", default="docs/evidence/live-interactions")
    args = parser.parse_args()
    print(json.dumps(run(args.url, Path(args.out)), indent=2))


if __name__ == "__main__":
    main()
