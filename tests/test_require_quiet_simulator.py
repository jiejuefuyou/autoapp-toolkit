from __future__ import annotations

import contextlib
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import require_quiet_simulator as quiet  # noqa: E402


UDID = "11111111-2222-4333-8444-555555555555"
OTHER_UDID = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"


class RequireQuietSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="quiet-schema-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.devices = self.base / "Devices"
        self.device = self.devices / UDID
        self.settings = self.device / "data/var/run/simulatoraudio/audiosettings.plist"
        self.settings.parent.mkdir(parents=True)

    def write(self, value: object) -> bytes:
        content = plistlib.dumps(value)
        self.settings.write_bytes(content)
        return content

    def require(self, udid: object = UDID) -> dict:
        return quiet.require_quiet_simulator(udid, devices_root=self.devices)

    def test_numeric_zero_receipt_identifies_exact_device_and_settings(self) -> None:
        for volume in (0, 0.0, -0.0):
            with self.subTest(volume=volume):
                before = self.write({"sim_volume": volume, "unrelated": "preserved"})
                self.assertEqual(
                    self.require(),
                    {
                        "udid": UDID,
                        "volume": 0,
                        "audio_settings": str(self.settings.resolve()),
                        "state": "quiet",
                    },
                )
                self.assertEqual(self.settings.read_bytes(), before)

    def test_nonzero_or_unverified_volume_is_rejected_without_mutation(self) -> None:
        for volume in (1, -1, 0.01, True, False, "0", float("nan"), float("inf"), float("-inf")):
            with self.subTest(volume=repr(volume)):
                before = self.write({"sim_volume": volume})
                with self.assertRaises(quiet.QuietSimulatorError):
                    self.require()
                self.assertEqual(self.settings.read_bytes(), before)

    def test_missing_key_and_nondictionary_plists_are_rejected(self) -> None:
        for value in ({}, {"volume": 0}, [], [0], "0", 0):
            with self.subTest(value=value):
                self.write(value)
                with self.assertRaises(quiet.QuietSimulatorError):
                    self.require()

    def test_corrupt_plist_is_rejected_without_mutation(self) -> None:
        for content in (b"", b"not a plist", b"<plist><dict>", b"bplist00broken"):
            with self.subTest(content=content):
                self.settings.write_bytes(content)
                with self.assertRaises(quiet.QuietSimulatorError):
                    self.require()
                self.assertEqual(self.settings.read_bytes(), content)

    def test_missing_settings_or_root_is_rejected(self) -> None:
        with self.assertRaises(quiet.QuietSimulatorError):
            self.require()
        with self.assertRaises(quiet.QuietSimulatorError):
            quiet.require_quiet_simulator(UDID, devices_root=self.base / "absent")

    def test_invalid_uuid_cannot_select_another_path(self) -> None:
        self.write({"sim_volume": 0})
        for udid in (None, False, "", "../" + UDID, str(self.device), UDID + "/..", " " + UDID, UDID + "\n", "{" + UDID + "}", UDID.replace("-", "")):
            with self.subTest(udid=udid):
                with self.assertRaises(quiet.QuietSimulatorError):
                    self.require(udid)

    def test_lowercase_uuid_selects_canonical_uppercase_directory(self) -> None:
        target = self.devices / OTHER_UDID / "data/var/run/simulatoraudio/audiosettings.plist"
        target.parent.mkdir(parents=True)
        target.write_bytes(plistlib.dumps({"sim_volume": 0}))
        receipt = self.require(OTHER_UDID.lower())
        self.assertEqual(receipt["udid"], OTHER_UDID)
        self.assertEqual(receipt["audio_settings"], str(target.resolve()))

    def test_device_directory_cannot_redirect_outside_devices_root(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        linked_device = self.devices / OTHER_UDID
        linked_device.symlink_to(outside, target_is_directory=True)
        target = outside / "data/var/run/simulatoraudio/audiosettings.plist"
        target.parent.mkdir(parents=True)
        target.write_bytes(plistlib.dumps({"sim_volume": 0}))
        with self.assertRaises(quiet.QuietSimulatorError):
            self.require(OTHER_UDID)

    def test_audio_settings_cannot_link_to_another_device_or_host_path(self) -> None:
        for target in (self.devices / OTHER_UDID / "settings.plist", self.base / "host.plist"):
            with self.subTest(target=target):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(plistlib.dumps({"sim_volume": 0}))
                self.settings.symlink_to(target)
                with self.assertRaises(quiet.QuietSimulatorError):
                    self.require()
                self.settings.unlink()

    def test_var_link_outside_exact_device_is_rejected(self) -> None:
        self.settings.parent.rmdir()
        self.settings.parent.parent.rmdir()
        self.settings.parent.parent.parent.rmdir()
        outside = self.base / "outside-var"
        target = outside / "run/simulatoraudio/audiosettings.plist"
        target.parent.mkdir(parents=True)
        target.write_bytes(plistlib.dumps({"sim_volume": 0}))
        (self.device / "data/var").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(quiet.QuietSimulatorError):
            self.require()

    def test_coresimulator_internal_var_link_is_valid(self) -> None:
        self.settings.parent.rmdir()
        self.settings.parent.parent.rmdir()
        self.settings.parent.parent.parent.rmdir()
        private_var = self.device / "data/private/var"
        target = private_var / "run/simulatoraudio/audiosettings.plist"
        target.parent.mkdir(parents=True)
        target.write_bytes(plistlib.dumps({"sim_volume": 0}))
        (self.device / "data/var").symlink_to("private/var", target_is_directory=True)
        receipt = self.require()
        self.assertEqual(receipt["audio_settings"], str(target.resolve()))
        self.assertEqual(receipt["state"], "quiet")

    def test_symlink_cycle_is_rejected_with_guard_error(self) -> None:
        self.settings.symlink_to("audiosettings.plist")
        with self.assertRaises(quiet.QuietSimulatorError):
            self.require()

    def test_unbounded_xml_integer_is_rejected_with_guard_error(self) -> None:
        self.settings.write_bytes(
            b'<?xml version="1.0"?><plist version="1.0"><dict>'
            b'<key>sim_volume</key><integer>' + b"9" * 400 +
            b'</integer></dict></plist>'
        )
        with self.assertRaises(quiet.QuietSimulatorError):
            self.require()

    def test_cli_accepts_only_uuid_and_never_passes_a_fixture_root(self) -> None:
        expected = {"udid": UDID, "volume": 0, "audio_settings": "fixture", "state": "quiet"}
        output = io.StringIO()
        with patch.object(sys, "argv", [str(ROOT / "scripts/require_quiet_simulator.py"), UDID]), patch.object(quiet, "require_quiet_simulator", return_value=expected) as require, contextlib.redirect_stdout(output):
            self.assertEqual(quiet.main(), 0)
        require.assert_called_once_with(UDID)
        self.assertEqual(json.loads(output.getvalue()), expected)
        for extra in (("--devices-root", str(self.devices)), ("--root", str(self.devices)), (str(self.devices),)):
            with self.subTest(extra=extra):
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/require_quiet_simulator.py"), UDID, *extra],
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
                self.assertIn("unrecognized arguments", completed.stderr)

    def test_default_root_uses_system_account_record_and_current_uid(self) -> None:
        account_home = self.base / "system-account-home"
        with patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=str(account_home))) as lookup, patch.object(quiet.Path, "home", side_effect=AssertionError("Path.home must not select the production device root")):
            self.assertEqual(
                quiet._default_devices_root(),
                account_home / "Library/Developer/CoreSimulator/Devices",
            )
        lookup.assert_called_once_with(os.getuid())

    def test_cli_default_root_is_system_account_and_environment_cannot_supply_quiet_fixture(self) -> None:
        self.write({"sim_volume": 0})
        account_home = self.base / "empty-system-account-home"
        account_home.mkdir()
        errors = io.StringIO()
        with patch.object(sys, "argv", ["require_quiet_simulator.py", UDID]), patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=str(account_home))) as lookup, patch.object(quiet.Path, "home", side_effect=AssertionError("Path.home must not select the production device root")), patch.dict("os.environ", {
            "QUIET_SIMULATOR_DEVICES_ROOT": str(self.devices),
            "CORE_SIMULATOR_DEVICES_ROOT": str(self.devices),
            "SIMULATOR_DEVICE_ROOT": str(self.devices),
        }), contextlib.redirect_stderr(errors):
            self.assertEqual(quiet.main(), 2)
        lookup.assert_called_once_with(os.getuid())
        self.assertIn("TEST_AUDIO_NOT_QUIET", errors.getvalue())

    def test_cli_cannot_accept_quiet_settings_under_forged_home_environment(self) -> None:
        # This is an isolated child-process attack probe. The parent process's
        # HOME and all real user/device settings remain untouched.
        original_home = os.environ.get("HOME")
        forged_home = self.base / "forged-home"
        forged_root = forged_home / "Library/Developer/CoreSimulator/Devices"
        fake_uuid = str(uuid.uuid4()).upper()
        forged_settings = forged_root / fake_uuid / "data/var/run/simulatoraudio/audiosettings.plist"
        forged_settings.parent.mkdir(parents=True)
        content = plistlib.dumps({"sim_volume": 0})
        forged_settings.write_bytes(content)
        # Prove the forged fixture is readable and would otherwise look quiet.
        self.assertEqual(quiet.require_quiet_simulator(fake_uuid, devices_root=forged_root)["state"], "quiet")
        child_env = dict(os.environ)
        child_env["HOME"] = str(forged_home)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/require_quiet_simulator.py"), fake_uuid],
            env=child_env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertIn("TEST_AUDIO_NOT_QUIET", completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(os.environ.get("HOME"), original_home)
        self.assertEqual(forged_settings.read_bytes(), content)

    def test_cli_reports_guard_failure_as_exit_two(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/require_quiet_simulator.py"), "../invalid"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("TEST_AUDIO_NOT_QUIET", completed.stderr)
        self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main()
