from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asc_testflight_readiness as gate  # noqa: E402


class TestFlightReadinessTests(unittest.TestCase):
    def snapshot(self) -> dict:
        return {
            "build": {
                "processing_state": "VALID",
                "expired": False,
                "expiration_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
                "platform": "IOS",
                "audience": "APP_STORE_ELIGIBLE",
                "minimum_os": "17.0",
                "has_icon": True,
                "encryption_resolved": True,
                "internal_build_state": "IN_BETA_TESTING",
            },
            "group": {"is_internal": True, "build_access": True},
            "tester": {"in_group": True, "state": "ACCEPTED"},
            "asc_user": {
                "id": "user-1",
                "roles": ["APP_MANAGER"],
                "can_access_app": True,
            },
        }

    def test_complete_server_triangle_passes(self) -> None:
        checks = gate.evaluate_snapshot(self.snapshot())
        self.assertTrue(all(item["ok"] for item in checks), checks)

    def test_created_beta_tester_without_accepted_invitation_fails(self) -> None:
        snapshot = self.snapshot()
        snapshot["tester"]["state"] = "INVITED"
        checks = {item["name"]: item["ok"] for item in gate.evaluate_snapshot(snapshot)}
        self.assertFalse(checks["tester_accepted"])

    def test_beta_tester_without_asc_user_fails_internal_gate(self) -> None:
        snapshot = self.snapshot()
        snapshot["asc_user"] = {"id": None, "roles": [], "can_access_app": False}
        checks = {item["name"]: item["ok"] for item in gate.evaluate_snapshot(snapshot)}
        self.assertFalse(checks["tester_is_asc_user"])
        self.assertFalse(checks["eligible_internal_role"])
        self.assertFalse(checks["user_can_access_app"])

    def test_group_without_exact_build_fails(self) -> None:
        snapshot = self.snapshot()
        snapshot["group"]["build_access"] = False
        checks = {item["name"]: item["ok"] for item in gate.evaluate_snapshot(snapshot)}
        self.assertFalse(checks["group_grants_exact_build"])


if __name__ == "__main__":
    unittest.main()
