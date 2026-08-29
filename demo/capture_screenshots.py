# -*- coding: utf-8 -*-
"""Capture product screenshots for the finals deck into demo/assets/."""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173/app/"
JOB = "49952516"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "demo", "assets")
REPORT_HTML = "file:///" + os.path.join(ROOT, "storage", "jobs", JOB, "report.html").replace("\\", "/")

os.makedirs(OUT, exist_ok=True)


def shot(page, name):
    path = os.path.join(OUT, name)
    page.screenshot(path=path)
    print("saved", name)


def open_evidence(page, triage_label, type_chip=None):
    """Select triage x source card and open the first matching diff's evidence dialog."""
    cards = page.locator(".diff-drilldown-card")
    for i in range(cards.count()):
        card = cards.nth(i)
        tri = card.locator(".diff-triage-label").inner_text()
        src = card.locator(".diff-source-label").inner_text()
        if triage_label in tri and "A/H" in src:
            card.click()
            page.wait_for_timeout(600)
            break
    rows = page.locator(".diff-active-list article.diff-source-row")
    target = None
    for i in range(rows.count()):
        row = rows.nth(i)
        chip = row.locator(".type-chip").inner_text()
        if type_chip is None or type_chip in chip:
            target = row
            break
    if target is None:
        raise RuntimeError("no matching diff row for %s / %s" % (triage_label, type_chip))
    target.locator("button.ghost", has_text="查看证据").click()
    page.wait_for_selector(".review-shell", timeout=8000)
    page.wait_for_timeout(900)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge")
    page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)

    # 1. cockpit
    page.goto(BASE + "#/cockpit")
    page.wait_for_timeout(3500)
    shot(page, "cockpit.png")

    # 2. history
    page.goto(BASE + "#/history")
    page.wait_for_timeout(3000)
    shot(page, "history.png")

    # 3. job detail top (conclusion + KPIs)
    page.goto(BASE + "#/jobs/" + JOB)
    page.wait_for_timeout(4000)
    shot(page, "job-detail.png")

    # 4. diff board (scroll the triage x source card grid into view)
    page.locator("h2", has_text="差异与证据").scroll_into_view_if_needed()
    page.wait_for_timeout(800)
    shot(page, "diff-board.png")

    # 5. numeric diff evidence dialog (pending-review queue holds the numeric ones)
    open_evidence(page, "待人工复核差异", "数值差异")
    shot(page, "evidence-numeric.png")
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)

    # 6. expected-diff evidence dialog
    open_evidence(page, "预期差异")
    shot(page, "evidence-expected.png")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)

    # 7. standalone HTML report
    page.goto(REPORT_HTML)
    page.wait_for_timeout(2500)
    shot(page, "report-html.png")

    browser.close()

print("all screenshots done ->", OUT)
