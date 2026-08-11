#!/usr/bin/env python3
"""asc_live_lookup.py — credential-free LIVE App Store probe for the Mac.

The full ASC state monitor (asc_realtime_monitor.py, JWT-authed) lives on the
Windows box, not this Mac. This is the Mac-runnable substitute: it hits the
public iTunes Lookup API (no key, no login) to report which of the 8 autoapp
apps are actually LIVE on the store and at what version, in any territory.

What it CANNOT see (needs the JWT ASC scripts on Windows): in-review / rejected
state, IAP state, TestFlight builds. Absent from the store here == "not live
yet" (in review or never released), NOT necessarily rejected.

Usage:
  python3 asc_live_lookup.py                 # US + JP
  python3 asc_live_lookup.py us jp cn kr     # explicit territories
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

APPS: list[tuple[str, str]] = [
    ("AutoChoice", "com.jiejuefuyou.autochoice"),
    ("AltitudeNow", "com.jiejuefuyou.altitudenow"),
    ("DaysUntil", "com.jiejuefuyou.daysuntil"),
    ("PromptVault", "com.jiejuefuyou.promptvault"),
    ("HabitHash", "com.jiejuefuyou.habithash"),
    ("FocusFlowLite", "com.jiejuefuyou.focusflow"),
    ("TipJarNow", "com.jiejuefuyou.tipjarnow"),
    ("WaterNow", "com.jiejuefuyou.waternow"),
]


def lookup(bundle_id: str, country: str) -> tuple[str | None, str | None]:
    """Return (version, release_date) if live in `country`, else (None, None)."""
    url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(
        {"bundleId": bundle_id, "country": country}
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.load(resp)
    except Exception as exc:  # network / parse — report, don't crash the sweep
        return (f"ERR:{type(exc).__name__}", None)
    if data.get("resultCount", 0) > 0:
        r = data["results"][0]
        return (r.get("version"), (r.get("currentVersionReleaseDate") or "")[:10])
    return (None, None)


def main(argv: list[str]) -> int:
    countries = [c.lower() for c in argv[1:]] or ["us", "jp"]
    head = f"{'app':14}" + "".join(f"{c.upper():>10}" for c in countries) + "   rel(1st)"
    print(head)
    print("-" * len(head))
    live = 0
    for name, bid in APPS:
        cells: list[str] = []
        rel = ""
        any_live = False
        for c in countries:
            ver, rdate = lookup(bid, c)
            cells.append(str(ver))
            if ver and not str(ver).startswith("ERR"):
                any_live = True
                rel = rel or (rdate or "")
        live += 1 if any_live else 0
        print(f"{name:14}" + "".join(f"{v:>10}" for v in cells) + f"   {rel}")
    print("-" * len(head))
    print(f"LIVE on store: {live}/{len(APPS)} territories={','.join(countries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
