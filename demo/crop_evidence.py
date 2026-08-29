# -*- coding: utf-8 -*-
"""Crop evidence-numeric.png to the evidence-dialog bounding box (measured live)."""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from playwright.sync_api import sync_playwright
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "demo", "assets")
BASE = "http://localhost:5173/app/"
JOB = "49952516"

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge")
    page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
    page.goto(BASE + "#/jobs/" + JOB)
    page.wait_for_timeout(4000)

    cards = page.locator(".diff-drilldown-card")
    for i in range(cards.count()):
        card = cards.nth(i)
        tri = card.locator(".diff-triage-label").inner_text()
        src = card.locator(".diff-source-label").inner_text()
        if "待人工复核差异" in tri and "A/H" in src:
            card.click()
            page.wait_for_timeout(600)
            break
    rows = page.locator(".diff-active-list article.diff-source-row")
    for i in range(rows.count()):
        row = rows.nth(i)
        if "数值差异" in row.locator(".type-chip").inner_text():
            row.locator("button.ghost", has_text="查看证据").click()
            break
    page.wait_for_selector(".review-shell", timeout=8000)
    page.wait_for_timeout(900)
    box = page.locator(".review-shell").bounding_box()
    print("dialog box (logical px):", box)
    browser.close()

# image is 2x logical
x0 = max(0, int(box["x"] * 2) - 24)
y0 = max(0, int(box["y"] * 2) - 24)
x1 = int((box["x"] + box["width"]) * 2) + 24
y1 = int((box["y"] + box["height"]) * 2) + 24

src = Image.open(os.path.join(ASSETS, "evidence-numeric.png"))
x1 = min(x1, src.width); y1 = min(y1, src.height)
crop = src.crop((x0, y0, x1, y1))
out = os.path.join(ASSETS, "evidence-numeric-crop.png")
crop.save(out)
print("saved evidence-numeric-crop.png", crop.size, "aspect %.3f" % (crop.width / crop.height))
