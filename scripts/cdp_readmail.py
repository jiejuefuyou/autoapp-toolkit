import sys
from playwright.sync_api import sync_playwright
needle = sys.argv[1] if len(sys.argv) > 1 else "AltitudeNowPro"
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://localhost:9222")
    page = b.contexts[0].pages[0]
    try:
        page.get_by_text(needle, exact=False).first.click(timeout=15000)
    except Exception as e:
        print("CLICK_FAIL:", e); sys.exit(1)
    page.wait_for_timeout(3500)
    print(page.inner_text("body"))
