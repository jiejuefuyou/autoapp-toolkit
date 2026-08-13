from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asc_monitor  # noqa: E402


class ASCMonitorTests(unittest.TestCase):
    def test_mint_token_uses_only_macos_openssl_and_emits_raw_es256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "AuthKey_TEST.p8"
            subprocess.run(
                [
                    "/usr/bin/openssl",
                    "ecparam",
                    "-name",
                    "prime256v1",
                    "-genkey",
                    "-noout",
                    "-out",
                    str(key_path),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            token = asc_monitor.mint_token("issuer", "TEST", key_path)
        segments = token.split(".")
        self.assertEqual(len(segments), 3)
        signature = base64.urlsafe_b64decode(segments[2] + "==")
        self.assertEqual(len(signature), 64)
        header = json.loads(base64.urlsafe_b64decode(segments[0] + "=="))
        self.assertEqual(header, {"alg": "ES256", "kid": "TEST", "typ": "JWT"})

    def test_api_url_encodes_structured_query_parameters(self) -> None:
        url = asc_monitor.api_url(
            "/v1/apps",
            {"filter[bundleId]": "com.example.app", "include": ["builds", "reviewSubmissions"]},
        )
        self.assertIn("filter%5BbundleId%5D=com.example.app", url)
        self.assertIn("include=builds", url)
        self.assertIn("include=reviewSubmissions", url)

    def test_snapshot_preserves_release_ids_and_states(self) -> None:
        report = {
            "polled_at": "2026-08-13T00:00:00+00:00",
            "app": {"id": "app-1", "name": "AltitudeNowPro", "bundle_id": "com.example.an"},
            "versions": [
                {
                    "id": "version-1",
                    "version": "1.0.6",
                    "state": "WAITING_FOR_REVIEW",
                    "platform": "IOS",
                    "created_date": "2026-08-13T00:00:00Z",
                    "release_type": "MANUAL",
                    "selected_build": {"id": "build-1", "build": "6"},
                }
            ],
            "builds": [{"id": "build-1", "build": "6", "train": "1.0.6"}],
            "in_app_purchases": [
                {
                    "id": "iap-1",
                    "name": "premium",
                    "product_id": "com.example.an.premium",
                    "type": "NON_CONSUMABLE",
                    "state": "WAITING_FOR_REVIEW",
                    "review_note": "Open Settings, then Unlock Premium.",
                    "versions": [
                        {
                            "id": "iap-version-1",
                            "version": 2,
                            "state": "WAITING_FOR_REVIEW",
                            "localizations": [],
                        }
                    ],
                    "localizations": [],
                    "base_price": {"currency": "USD", "customer_price": "2.99"},
                    "availability": {"territory_count": 175},
                    "review_screenshot": {
                        "id": "screenshot-1",
                        "delivery_state": "COMPLETE",
                    },
                }
            ],
            "review_submissions": [
                {
                    "id": "submission-1",
                    "state": "WAITING_FOR_REVIEW",
                    "submitted_date": "2026-08-13T00:00:00Z",
                    "platform": "IOS",
                    "version": "1.0.6",
                    "app_store_version_id": "version-1",
                    "items": [],
                }
            ],
        }
        snapshot = asc_monitor.portfolio_snapshot([report])
        app = snapshot["apps"][0]
        self.assertEqual(app["versions"][0]["version_id"], "version-1")
        self.assertEqual(app["versions"][0]["appStoreState"], "WAITING_FOR_REVIEW")
        self.assertEqual(app["builds"][0]["train"], "1.0.6")
        self.assertEqual(app["iaps"][0]["state"], "WAITING_FOR_REVIEW")
        self.assertEqual(app["iaps"][0]["versions"][0]["id"], "iap-version-1")
        self.assertEqual(app["iaps"][0]["base_price"]["customer_price"], "2.99")
        self.assertEqual(app["iaps"][0]["availability"]["territory_count"], 175)
        self.assertEqual(app["review_submissions"][0]["submission_id"], "submission-1")

    def test_snapshot_write_is_parseable_and_replaces_old_content(self) -> None:
        payload = {"monitor": "test", "polled_at": "now", "apps": []}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "nested" / "state.json"
            destination.parent.mkdir(parents=True)
            destination.write_text("stale", encoding="utf-8")
            asc_monitor.write_snapshot(destination, payload)
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), payload)


if __name__ == "__main__":
    unittest.main()
