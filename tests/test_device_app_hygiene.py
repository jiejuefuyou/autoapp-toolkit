from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import device_app_hygiene as hygiene  # noqa: E402


class DeviceAppHygieneTests(unittest.TestCase):
    def test_only_same_bundle_family_xctrunner_is_cleanup_candidate(self) -> None:
        bundle = "com.example.app"
        runner = {
            "name": "AppPhysicalUITests-Runner",
            "bundleIdentifier": "com.example.app.physical-uitests.xctrunner",
            "builtByDeveloper": True,
        }
        self.assertTrue(hygiene.is_test_runner(runner, bundle))
        self.assertFalse(
            hygiene.is_test_runner(
                {**runner, "bundleIdentifier": "com.example.other.physical-uitests.xctrunner"},
                bundle,
            )
        )
        self.assertFalse(
            hygiene.is_test_runner({**runner, "bundleIdentifier": bundle}, bundle)
        )
        self.assertFalse(
            hygiene.is_test_runner({**runner, "builtByDeveloper": False}, bundle)
        )

    def test_exact_direct_app_passes_and_runner_is_reported(self) -> None:
        bundle = "com.example.app"
        app = {
            "name": "App",
            "bundleIdentifier": bundle,
            "version": "1.2.3",
            "bundleVersion": "42",
            "builtByDeveloper": True,
        }
        runner = {
            "name": "AppUITests-Runner",
            "bundleIdentifier": bundle + ".uitests.xctrunner",
            "version": "1.0",
            "bundleVersion": "1",
            "builtByDeveloper": True,
        }
        selected, runners = hygiene.evaluate_app(
            [app, runner],
            bundle_id=bundle,
            version="1.2.3",
            build="42",
            distribution="direct",
        )
        self.assertEqual(selected, app)
        self.assertEqual(runners, [runner])

    def test_testflight_origin_rejects_developer_build(self) -> None:
        app = {
            "bundleIdentifier": "com.example.app",
            "version": "1.0",
            "bundleVersion": "1",
            "builtByDeveloper": True,
        }
        with self.assertRaises(hygiene.DeviceGateError):
            hygiene.evaluate_app(
                [app],
                bundle_id="com.example.app",
                version="1.0",
                build="1",
                distribution="testflight",
            )


if __name__ == "__main__":
    unittest.main()
