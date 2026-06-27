#!/usr/bin/env python3
"""cdp_fetch.py <url> [--screenshot out.png]  — 连已开的 CDP Chrome,导航并抽正文文本。"""
import sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
shot = sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--screenshot" else None
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://localhost:9222")
    ctx = b.contexts[0] if b.contexts else b.new_context()
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    if shot: page.screenshot(path=shot, full_page=True)
    print(page.inner_text("body")[:8000])
