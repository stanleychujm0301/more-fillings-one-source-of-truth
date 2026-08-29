# -*- coding: utf-8 -*-
"""Re-capture deck product screenshots from the live Zeabur deployment (v2).

Live UI notes (result_version 16):
- drilldown cards: .diff-drilldown-card, triage text in .diff-triage-label
- row type chips are 披露差异/… (no 数值差异 chip); pick rows whose meta shows numbers
- evidence dialog selector probed at runtime
"""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from playwright.sync_api import sync_playwright
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "demo", "assets")
BASE = "https://stanleyc-more-fillings-one-source-of-truth.preview.aliyun-zeabur.cn"
JOB = "06839f28"  # 光大银行 2025年 A/H on live


def shot_dialog(page, name):
    page.wait_for_selector(".review-shell", timeout=20000)
    page.wait_for_timeout(1500)
    page.screenshot(path=os.path.join(ASSETS, name))
    box = page.locator(".review-shell").bounding_box()
    print(name, "saved; dialog box:", box)
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    return box


def open_queue_card(page, triage_kw, source_kw):
    page.wait_for_selector(".diff-drilldown-card", timeout=30000)
    cards = page.locator(".diff-drilldown-card")
    for i in range(cards.count()):
        card = cards.nth(i)
        text = card.inner_text()
        if triage_kw in text and source_kw in text:
            card.click()
            page.wait_for_timeout(1000)
            print("clicked card:", text.replace("\n", " "))
            return True
    raise RuntimeError("no card: %s / %s" % (triage_kw, source_kw))


def click_first_numeric_row(page):
    rows = page.locator(".diff-active-list article.diff-source-row")
    n = rows.count()
    print("rows in queue:", n)
    for i in range(n):
        meta = rows.nth(i).locator(".diff-source-row-meta").inner_text()
        if any(ch.isdigit() for ch in meta):
            rows.nth(i).locator("button.ghost", has_text="查看证据").click()
            print("clicked row", i, "meta:", meta.replace("\n", " | ")[:80])
            return
    rows.first.locator("button.ghost", has_text="查看证据").click()
    print("clicked row 0 (fallback)")


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge")
    page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))

    # 1. cockpit
    page.goto(BASE + "/#/cockpit", wait_until="networkidle", timeout=90000)
    page.wait_for_timeout(2500)
    page.screenshot(path=os.path.join(ASSETS, "cockpit.png"))
    print("cockpit.png saved")

    # 2. job detail (wait for real content, not the loading skeleton)
    page.goto(BASE + "/#/jobs/" + JOB, wait_until="networkidle", timeout=90000)
    page.wait_for_selector(".diff-drilldown-card", timeout=60000)
    page.wait_for_timeout(2500)
    page.screenshot(path=os.path.join(ASSETS, "job-detail.png"))
    print("job-detail.png saved")

    # 3. numeric evidence: 真实差异 · A/H报告不一致, first row with numeric meta
    open_queue_card(page, "真实差异", "A/H")
    click_first_numeric_row(page)
    num_box = shot_dialog(page, "evidence-numeric.png")

    # 4. expected evidence: 预期差异 · A/H报告不一致
    open_queue_card(page, "预期差异", "A/H")
    click_first_numeric_row(page)
    shot_dialog(page, "evidence-expected.png")

    browser.close()

print("page errors:", errs if errs else "none")

# 5. crop numeric evidence to dialog bbox (2x dsf, 24px margin)
x0 = max(0, int(num_box["x"] * 2) - 24)
y0 = max(0, int(num_box["y"] * 2) - 24)
x1 = int((num_box["x"] + num_box["width"]) * 2) + 24
y1 = int((num_box["y"] + num_box["height"]) * 2) + 24
src = Image.open(os.path.join(ASSETS, "evidence-numeric.png"))
x1 = min(x1, src.width)
y1 = min(y1, src.height)
crop = src.crop((x0, y0, x1, y1))
crop.save(os.path.join(ASSETS, "evidence-numeric-crop.png"))
print("evidence-numeric-crop.png saved", crop.size, "aspect %.3f" % (crop.width / crop.height))

# 6. pad both full evidence screenshots to the S15 grid cell aspect (~2.15) with the
#    modal-backdrop gray, plus top headroom so the cell tag doesn't cover dialog titles
for name in ("evidence-numeric.png", "evidence-expected.png"):
    src = Image.open(os.path.join(ASSETS, name)).convert("RGB")
    w, h = src.size
    new_h = h + 220
    new_w = int(new_h * 2.15)
    canvas = Image.new("RGB", (new_w, new_h), (138, 144, 150))
    canvas.paste(src, ((new_w - w) // 2, 220))
    canvas.save(os.path.join(ASSETS, name))
    print(name, "padded to", canvas.size)
