# -*- coding: utf-8 -*-
"""Render every slide of finals-deck.html to demo/preview/ for visual verification."""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECK = "file:///" + os.path.join(ROOT, "demo", "finals-deck.html").replace("\\", "/")
OUT = os.path.join(ROOT, "demo", "preview")
TOTAL = 15

os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge")
    page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)

    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    for i in range(1, TOTAL + 1):
        page.goto(DECK + "#" + str(i))
        page.wait_for_timeout(1400)  # let entry animations + count-up finish
        page.screenshot(path=os.path.join(OUT, "s%02d.png" % i))
        print("saved s%02d.png" % i)

    # keyboard nav sanity: start at 1, End -> 13, Home -> 1, ArrowRight -> 2
    page.goto(DECK)
    page.wait_for_timeout(600)
    page.keyboard.press("End"); page.wait_for_timeout(500)
    assert page.locator("#pageno").inner_text().startswith("15"), "End key failed"
    page.keyboard.press("Home"); page.wait_for_timeout(500)
    assert page.locator("#pageno").inner_text().startswith("1"), "Home key failed"
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(500)
    assert page.locator("#pageno").inner_text().startswith("2"), "ArrowRight failed"
    page.keyboard.press("ArrowLeft"); page.wait_for_timeout(500)
    assert page.locator("#pageno").inner_text().startswith("1"), "ArrowLeft failed"
    print("keyboard nav OK")

    # offline context: no external requests, no broken images
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, offline=True)
    op = ctx.new_page()
    reqs = []
    op.on("request", lambda r: reqs.append(r.url))
    op.on("pageerror", lambda e: errors.append("offline: " + str(e)))
    for i in range(1, TOTAL + 1):
        op.goto(DECK + "#" + str(i))
        op.wait_for_timeout(300)
    bad = [u for u in reqs if not u.startswith("file://") and not u.startswith("data:")]
    broken = op.evaluate(
        "Array.from(document.images).filter(i=>!i.complete||i.naturalWidth===0).map(i=>i.getAttribute('src'))")
    print("offline external requests:", bad)
    print("broken images:", broken)
    ctx.close()

    browser.close()

print("JS/console errors:", errors if errors else "none")
