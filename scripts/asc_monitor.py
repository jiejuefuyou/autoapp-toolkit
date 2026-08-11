#!/usr/bin/env python3
"""asc_monitor.py — Mac-native App Store Connect state probe (JWT-authed).

The Mac port of the Windows `asc_realtime_monitor.py`: the "first action LIVE
probe" from the boot sequence. Reads the real ASC state for every app —
live version + any in-flight version and its review state — straight from the
ASC API, no browser, no CDP.

Auth (private .p8 key already lives on this Mac):
  - private key:  ~/.appstoreconnect/private_keys/AuthKey_<KEYID>.p8
  - key id:       env ASC_KEY_ID  OR  ~/.appstoreconnect/config.json  OR  auto from the .p8 filename
  - issuer id:    env ASC_ISSUER_ID  OR  ~/.appstoreconnect/config.json  (UUID; the only thing not on disk)

Set the issuer once (not a secret — a UUID from ASC > Users and Access > Keys):
  echo '{"issuer_id":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","key_id":"3X8QYT8TJR"}' > ~/.appstoreconnect/config.json

Usage (via the toolkit venv):
  autoapp-toolkit/.venv/bin/python autoapp-toolkit/scripts/asc_monitor.py [--app NAME]
  # or the wrapper:  bash autoapp-toolkit/scripts/asc-monitor.sh [--app NAME]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt  # PyJWT[crypto] in the toolkit venv

ASC_BASE = "https://api.appstoreconnect.apple.com"
KEYDIR = Path.home() / ".appstoreconnect" / "private_keys"
CONFIG = Path.home() / ".appstoreconnect" / "config.json"
# the most relevant state field changed names across API versions; read whichever is present
STATE_FIELDS = ("appStoreState", "appVersionState", "state")


def load_creds() -> tuple[str, str, str]:
    """Return (issuer_id, key_id, private_key_pem). Exit 2 with guidance if incomplete."""
    cfg: dict[str, str] = {}
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text())
        except Exception as exc:
            print(f"WARN: {CONFIG} unreadable ({exc}); ignoring", file=sys.stderr)

    key_id = os.environ.get("ASC_KEY_ID") or cfg.get("key_id") or ""
    if not key_id:
        keys = glob.glob(str(KEYDIR / "AuthKey_*.p8"))
        if len(keys) == 1:
            key_id = Path(keys[0]).stem.replace("AuthKey_", "")

    issuer = os.environ.get("ASC_ISSUER_ID") or cfg.get("issuer_id") or ""

    keyfile = KEYDIR / f"AuthKey_{key_id}.p8"
    missing = []
    if not issuer or issuer.startswith("xxxx"):
        missing.append("issuer id (env ASC_ISSUER_ID or 'issuer_id' in ~/.appstoreconnect/config.json)")
    if not key_id:
        missing.append("key id (no single .p8 found to auto-detect)")
    if key_id and not keyfile.exists():
        missing.append(f"private key file {keyfile}")
    if missing:
        print("ASC creds incomplete — need:\n  - " + "\n  - ".join(missing), file=sys.stderr)
        print("\nThe issuer id is a UUID from ASC > Users and Access > Keys (not a secret).", file=sys.stderr)
        sys.exit(2)

    return issuer, key_id, keyfile.read_text()


def mint_token(issuer: str, key_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": issuer, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        private_key_pem,
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


def api_get(token: str, path: str) -> dict:
    req = urllib.request.Request(ASC_BASE + path, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            print("AUTH FAILED (401) — issuer id wrong or key revoked. Verify ASC_ISSUER_ID.", file=sys.stderr)
            sys.exit(3)
        raise SystemExit(f"ASC API {exc.code} on {path}: {body}")


def state_of(attrs: dict) -> str:
    for f in STATE_FIELDS:
        if attrs.get(f):
            return str(attrs[f])
    return "?"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", help="filter to one app by (case-insensitive) name substring")
    args = ap.parse_args(argv[1:])

    issuer, key_id, pem = load_creds()
    token = mint_token(issuer, key_id, pem)

    apps = api_get(token, "/v1/apps?limit=200&fields[apps]=name,bundleId").get("data", [])
    rows: list[tuple[str, str, str, str]] = []
    for a in sorted(apps, key=lambda x: x["attributes"].get("name", "")):
        name = a["attributes"].get("name", "?")
        if args.app and args.app.lower() not in name.lower():
            continue
        bid = a["attributes"].get("bundleId", "?")
        vers = api_get(token, f"/v1/apps/{a['id']}/appStoreVersions?limit=10").get("data", [])
        live = next((v["attributes"].get("versionString", "?") for v in vers
                     if state_of(v["attributes"]) == "READY_FOR_SALE"), "—")
        inflight = next((f'{v["attributes"].get("versionString","?")} [{state_of(v["attributes"])}]'
                         for v in vers if state_of(v["attributes"]) != "READY_FOR_SALE"), "—")
        rows.append((name, bid, live, inflight))

    print(f"{'app':16}{'live':>10}   in-flight (state)")
    print("-" * 70)
    for name, _bid, live, inflight in rows:
        print(f"{name:16}{live:>10}   {inflight}")
    print("-" * 70)
    live_n = sum(1 for r in rows if r[2] != "—")
    print(f"{live_n}/{len(rows)} LIVE on store   (ASC API, probed {time.strftime('%Y-%m-%d %H:%M')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
