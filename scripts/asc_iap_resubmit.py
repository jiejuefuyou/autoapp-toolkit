#!/usr/bin/env python3
"""asc_iap_resubmit.py — Mac-native fix for an IAP stuck in DEVELOPER_ACTION_NEEDED
with locale-level REJECTED localizations (CLAUDE.md §4.2).

REJECTED IAP localizations cannot be PATCHed (409 UNMODIFIABLE). The documented
fix is: DELETE each REJECTED localization, recreate it with the same content
(-> PREPARE), then POST an inAppPurchaseSubmission (-> WAITING_FOR_REVIEW). This
is the all-API path for a non-first IAP (one that has been through review before,
which a REJECTED state proves). If Apple still requires it to ride with a version
(FIRST_IAP_MUST_BE_SUBMITTED_ON_VERSION), the script stops and says so rather than
touching the app's in-flight review submission.

Default is DRY-RUN. Pass --execute to mutate. Original localization content is
captured to a backup file before any DELETE.

Usage (toolkit venv):
  .venv/bin/python scripts/asc_iap_resubmit.py --app AltitudeNow            # dry-run
  .venv/bin/python scripts/asc_iap_resubmit.py --app AltitudeNow --execute  # do it
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asc_monitor as m  # reuse auth + GET

BACKUP = Path(__file__).resolve().parent.parent / "logs" / "iap_loc_backup.json"


def _req(token: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(m.ASC_BASE + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        return exc.code, (json.loads(detail) if detail.strip().startswith("{") else {"raw": detail[:400]})


def err_codes(payload: dict | None) -> str:
    if not payload or "errors" not in payload:
        return ""
    return "; ".join(f"{e.get('code')}:{e.get('detail','')[:80]}" for e in payload["errors"])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", help="app name substring (e.g. AltitudeNow)")
    ap.add_argument("--iap", help="IAP id (skips app lookup)")
    ap.add_argument("--execute", action="store_true", help="actually mutate (default: dry-run)")
    args = ap.parse_args(argv[1:])

    token = m.mint_token(*m.load_creds())

    iid = args.iap
    if not iid:
        if not args.app:
            print("need --app or --iap", file=sys.stderr); return 2
        apps = m.api_get(token, "/v1/apps?limit=200&fields[apps]=name,bundleId")["data"]
        app = next((a for a in apps if args.app.lower() in a["attributes"].get("name", "").lower()
                    or args.app.lower() in a["attributes"].get("bundleId", "").lower()), None)
        if not app:
            print(f"no app matching {args.app!r}", file=sys.stderr); return 2
        iaps = m.api_get(token, f"/v1/apps/{app['id']}/inAppPurchasesV2?limit=10")["data"]
        if not iaps:
            print("app has no IAP", file=sys.stderr); return 2
        iid = iaps[0]["id"]
        print(f"app {app['attributes']['name']} -> IAP {iaps[0]['attributes'].get('productId')} ({iid})")

    iap = m.api_get(token, f"/v2/inAppPurchases/{iid}")["data"]
    print(f"IAP state = {iap['attributes'].get('state')}")

    locs = m.api_get(token, f"/v2/inAppPurchases/{iid}/inAppPurchaseLocalizations?limit=50")["data"]
    rejected = [l for l in locs if l["attributes"].get("state") == "REJECTED"]
    print(f"localizations: {len(locs)} total, {len(rejected)} REJECTED")
    for l in rejected:
        a = l["attributes"]
        print(f"  - {a['locale']}: {a['name']!r} / {(a.get('description') or '')!r}")

    if not rejected:
        print("nothing REJECTED to rebuild.")
        return 0

    if not args.execute:
        print("\nDRY-RUN. Would: DELETE+recreate the REJECTED localizations above, "
              "then POST inAppPurchaseSubmissions. Re-run with --execute.")
        return 0

    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    BACKUP.write_text(json.dumps([l["attributes"] for l in rejected], ensure_ascii=False, indent=1))
    print(f"\nbacked up {len(rejected)} localizations -> {BACKUP}")

    for l in rejected:
        a = l["attributes"]
        loc, name, desc = a["locale"], a["name"], a.get("description")
        code, _ = _req(token, "DELETE", f"/v1/inAppPurchaseLocalizations/{l['id']}")
        print(f"  DELETE {loc}: {code}" + ("" if code in (204, 200) else f" (continuing; §4.2 tolerates 500)"))
        attrs = {"locale": loc, "name": name}
        if desc:
            attrs["description"] = desc
        body = {"data": {"type": "inAppPurchaseLocalizations", "attributes": attrs,
                         "relationships": {"inAppPurchaseV2": {"data": {"type": "inAppPurchases", "id": iid}}}}}
        code, payload = _req(token, "POST", "/v1/inAppPurchaseLocalizations", body)
        print(f"  POST   {loc}: {code} {err_codes(payload)}")
        if code not in (200, 201):
            print(f"  ABORT: recreate failed for {loc}. Backup at {BACKUP}.", file=sys.stderr)
            return 1

    # submit the IAP for review
    sub = {"data": {"type": "inAppPurchaseSubmissions",
                    "relationships": {"inAppPurchaseV2": {"data": {"type": "inAppPurchases", "id": iid}}}}}
    code, payload = _req(token, "POST", "/v1/inAppPurchaseSubmissions", sub)
    print(f"\nPOST inAppPurchaseSubmissions: {code} {err_codes(payload)}")
    if code in (200, 201):
        time.sleep(2)
        final = m.api_get(token, f"/v2/inAppPurchases/{iid}")["data"]["attributes"].get("state")
        print(f"IAP state now: {final}")
        return 0 if final in ("WAITING_FOR_REVIEW", "IN_REVIEW") else 0
    codes = err_codes(payload)
    if "FIRST_IAP_MUST_BE_SUBMITTED_ON_VERSION" in codes:
        print("STOP: Apple requires this (first-ever) IAP to be submitted WITH the app version.\n"
              "The app RS is currently WAITING_FOR_REVIEW — cancelling/re-submitting it is a bigger\n"
              "call (loses queue position). Localizations are now rebuilt to PREPARE; coordinate the\n"
              "app+IAP back-to-back submission (CLAUDE.md §4.2 never-LIVE first-IAP) before deciding.",
              file=sys.stderr)
        return 3
    print(f"submission failed: {codes or payload}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
