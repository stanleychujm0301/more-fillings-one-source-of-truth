# -*- coding: utf-8 -*-
"""只重截 demo/assets/history.png（项目组共享历史，混合提交人版）。

前置：本地服务已启动（uvicorn :8000），且已执行 demo/seed_sharing_history.py。
用法：python demo/capture_history.py
"""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "demo", "assets", "history.png")

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge")
    page = browser.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
    page.goto(BASE + "/app/")
    page.wait_for_timeout(2500)

    # 未登录会被送到登录页：填表演示账号 stanleychu
    if page.locator("input[type=password]").count():
        page.locator("input:not([type=password])").first.fill("stanleychu")
        page.locator("input[type=password]").first.fill("demo1234")
        page.get_by_role("button", name="登录").click()
        page.wait_for_timeout(3500)
    print("after login url:", page.url)

    page.goto(BASE + "/app/#/history")
    page.wait_for_timeout(3000)
    page.screenshot(path=OUT)
    print("saved", OUT)

    # 快速自检：页面上应同时出现两个提交人
    body = page.inner_text("body")
    print("has Yu, Jill:", "Yu, Jill" in body, "| has Chu, Stanley:", "Chu, Stanley" in body)
    browser.close()
