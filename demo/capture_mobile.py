# -*- coding: utf-8 -*-
"""从线上 Zeabur 实例截取移动端视图（iPhone 尺寸仿真，等效 Edge 开发者工具移动端）。
产出 assets/mobile-job.png：手机视口下的任务详情页。"""
from playwright.sync_api import sync_playwright

BASE = "https://stanleyc-more-fillings-one-source-of-truth.preview.aliyun-zeabur.cn"
JOB = "06839f28"
OUT = "assets/mobile-job.png"

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge")
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844},   # iPhone 14 逻辑尺寸
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"),
    )
    page = ctx.new_page()
    page.goto(f"{BASE}/#/jobs/{JOB}", wait_until="networkidle", timeout=120000)
    try:
        page.wait_for_selector(".diff-drilldown-card", timeout=60000)
    except Exception:
        pass  # 移动端布局可能不同，退回等待网络空闲即可
    page.wait_for_timeout(2500)
    page.screenshot(path=OUT)
    print("saved", OUT, page.viewport_size)
    browser.close()
