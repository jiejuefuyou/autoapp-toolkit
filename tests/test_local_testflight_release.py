from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import local_testflight_release as release  # noqa: E402


class LocalTestFlightReleaseTests(unittest.TestCase):
    def test_validate_identity_accepts_exact_release_fields(self) -> None:
        release.validate_identity(
            bundle_id="com.example.product",
            version="1.2.3",
            build="42.1",
            git_sha="a" * 40,
            team_id="ABCDE12345",
        )

    def test_validate_identity_rejects_non_exact_version_and_sha(self) -> None:
        with self.assertRaises(release.ReleaseError):
            release.validate_identity(
                bundle_id="com.example.product",
                version="1.2",
                build="42",
                git_sha="abc",
                team_id="ABCDE12345",
            )

    def test_resolved_inside_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            with self.assertRaises(release.ReleaseError):
                release.resolved_inside(root, Path("../outside"), "output")

    def test_sha256_file_returns_complete_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.ipa"
            path.write_bytes(b"exact artifact")
            self.assertEqual(
                release.sha256_file(path), hashlib.sha256(b"exact artifact").hexdigest()
            )

    def test_asc_checks_require_valid_icon_encryption_and_expiration(self) -> None:
        build = {
            "processing_state": "VALID",
            "platform": "IOS",
            "expired": False,
            "expiration_date": (datetime.now(timezone.utc) + timedelta(days=80)).isoformat(),
            "minimum_os": "17.0",
            "has_icon": True,
            "uses_non_exempt_encryption": False,
            "audience": "APP_STORE_ELIGIBLE",
        }
        self.assertTrue(all(item["ok"] for item in release.asc_build_checks(build)))
        build["has_icon"] = False
        failed = [item["name"] for item in release.asc_build_checks(build) if not item["ok"]]
        self.assertEqual(failed, ["real_app_icon"])


if __name__ == "__main__":
    unittest.main()
