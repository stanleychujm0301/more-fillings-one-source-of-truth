# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECK = "file:///" + os.path.join(ROOT, "demo", "finals-deck.html").replace("\\", "/")

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge")
    pg = b.new_page(viewport={"width": 1600, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    for i in range(1, 17):
        pg.goto(DECK + "#" + str(i)); pg.wait_for_timeout(1300)
    pg.goto(DECK + "#11"); pg.wait_for_timeout(1500)
    vals = pg.evaluate("Array.from(document.querySelectorAll('#s11 .num')).map(e=>e.textContent)")
    print("S11 stats finals @1600x900:", vals)
    pg.goto(DECK + "#12"); pg.wait_for_timeout(1500)
    print("S12 speed final:", pg.evaluate("document.querySelector('#s12 .num').textContent"))
    print("S12 machine bar width:", pg.evaluate("getComputedStyle(document.querySelector('#s12 .fill.machine')).width"))
    pg.goto(DECK + "#9"); pg.wait_for_timeout(1200)
    print("S9 sharing cards:", pg.evaluate("document.querySelectorAll('#s9 .inno-card').length"))
    print("S9 shot loaded:", pg.evaluate("(document.querySelector('#s9 .inno-shot img')||{}).naturalWidth > 0"))
    pg.goto(DECK + "#7"); pg.wait_for_timeout(1500)
    print("S7 profile finals:", pg.evaluate("Array.from(document.querySelectorAll('#s7 .num')).map(e=>e.textContent)"))

    ctx = b.new_context(viewport={"width": 1920, "height": 1080}, reduced_motion="reduce")
    rp = ctx.new_page()
    rerrs = []
    rp.on("pageerror", lambda e: rerrs.append(str(e)))
    rp.goto(DECK + "#11"); rp.wait_for_timeout(400)
    print("reduced-motion S11 nums:", rp.evaluate("Array.from(document.querySelectorAll('#s11 .num')).map(e=>e.textContent)"))
    print("reduced-motion S11 stat opacity:", rp.evaluate("getComputedStyle(document.querySelector('#s11 .stat')).opacity"))
    rp.goto(DECK + "#12"); rp.wait_for_timeout(300)
    print("reduced-motion S12 machine bar:", rp.evaluate("getComputedStyle(document.querySelector('#s12 .fill.machine')).width"))
    rp.goto(DECK + "#7"); rp.wait_for_timeout(300)
    print("reduced-motion S7 nums:", rp.evaluate("Array.from(document.querySelectorAll('#s7 .num')).map(e=>e.textContent)"))
    rp.goto(DECK + "#2"); rp.wait_for_timeout(300)
    print("reduced-motion S2 num:", rp.evaluate("document.querySelector('#s2 .num').textContent"))
    ctx.close(); b.close()
    print("errors:", errs, rerrs)
