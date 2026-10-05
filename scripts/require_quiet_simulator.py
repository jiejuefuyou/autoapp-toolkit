#!/usr/bin/env python3
"""Fail closed unless the exact CoreSimulator test device has zero volume.

Device Hub's Sound control persists this value in simulatoraudio's settings.
Unknown or unavailable settings require configuring the device before testing;
this check never changes the Mac's output or invents simulator preferences.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import plistlib
import re
import sys
from pathlib import Path
from xml.parsers.expat import ExpatError


class QuietSimulatorError(RuntimeError):
    """The selected simulator's silence cannot be verified."""


def _default_devices_root() -> Path:
    # HOME is caller-controlled, including in hooks and launchd jobs. Only the
    # operating system's account record identifies the real user's devices.
    try:
        import pwd
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, AttributeError, KeyError, OSError) as exc:
        raise QuietSimulatorError("cannot determine the system account's device directory") from exc
    return home / "Library/Developer/CoreSimulator/Devices"


def require_quiet_simulator(udid: str, devices_root: Path | None = None) -> dict:
    # Injection is for pure unit tests only. The production CLI deliberately
    # exposes neither a root option nor an environment override.
    if not isinstance(udid, str) or not re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", udid
    ):
        raise QuietSimulatorError("invalid simulator UUID")
    udid = udid.upper()
    root = devices_root if devices_root is not None else _default_devices_root()
    try:
        root = Path(root).resolve(strict=True)
        device = root / udid
        resolved_device = device.resolve(strict=True)
        if resolved_device != device:
            raise QuietSimulatorError("simulator directory redirects to another device")
        settings = device / "data/var/run/simulatoraudio/audiosettings.plist"
        resolved_settings = settings.resolve(strict=True)
        # CoreSimulator may link data/var to data/private/var within this same
        # device. A link to any other device or host path is not valid evidence.
        try:
            resolved_settings.relative_to(device)
        except ValueError as exc:
            raise QuietSimulatorError("audio settings escape the selected device") from exc
        with resolved_settings.open("rb") as handle:
            values = plistlib.load(handle)
    except QuietSimulatorError:
        raise
    except (OSError, ValueError, RuntimeError, plistlib.InvalidFileException, ExpatError) as exc:
        raise QuietSimulatorError("audio settings are missing or unreadable") from exc
    if not isinstance(values, dict) or "sim_volume" not in values:
        raise QuietSimulatorError("audio settings have no verified sim_volume")
    volume = values["sim_volume"]
    if (type(volume) not in (int, float)
            or (type(volume) is float and not math.isfinite(volume))
            or volume != 0):
        raise QuietSimulatorError("sim_volume must be a finite numeric zero")
    return {
        "udid": udid,
        "volume": 0,
        "audio_settings": str(resolved_settings),
        "state": "quiet",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("udid", help="exact simulator UUID used by the test")
    args = parser.parse_args()
    try:
        receipt = require_quiet_simulator(args.udid)
    except QuietSimulatorError as exc:
        print(
            f"TEST_AUDIO_NOT_QUIET: {exc}. Set this exact device's Sound to 0 "
            "in Device Hub and verify it before retrying; this test phase was blocked.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
