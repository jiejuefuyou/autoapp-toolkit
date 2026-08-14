#!/usr/bin/env python3
"""Audit one physical iPhone app and remove only its XCTest runner shells."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DeviceGateError(RuntimeError):
    """The device state is unsafe, ambiguous, or not as requested."""


def is_test_runner(app: dict[str, Any], bundle_id: str) -> bool:
    candidate = str(app.get("bundleIdentifier") or "")
    name = str(app.get("name") or "")
    if app.get("builtByDeveloper") is not True or candidate == bundle_id:
        return False
    if not candidate.startswith(bundle_id + "."):
        return False
    lowered = f"{candidate} {name}".casefold()
    return any(token in lowered for token in ("xctrunner", "uitest", "ui-test", "tests-runner"))


def run(*command: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DeviceGateError(f"cannot run {' '.join(command)}: {error}") from error


def checked(*command: str) -> bytes:
    process = run(*command)
    if process.returncode != 0:
        detail = b" ".join((process.stdout, process.stderr)).decode("utf-8", "replace")
        raise DeviceGateError(f"command failed: {' '.join(command)}: {' '.join(detail.split())[:1200]}")
    return process.stdout


def devicectl_json(directory: Path, name: str, *arguments: str) -> dict[str, Any]:
    destination = directory / name
    checked("xcrun", "devicectl", *arguments, "--json-output", str(destination), "--quiet")
    try:
        payload = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DeviceGateError(f"cannot parse {destination}: {error}") from error
    if payload.get("info", {}).get("outcome") != "success":
        raise DeviceGateError(f"devicectl did not report success for {' '.join(arguments)}")
    return payload


def list_apps(directory: Path, device: str, name: str) -> list[dict[str, Any]]:
    payload = devicectl_json(
        directory,
        name,
        "device",
        "info",
        "apps",
        "--device",
        device,
        "--include-all-apps",
    )
    apps = payload.get("result", {}).get("apps")
    if not isinstance(apps, list):
        raise DeviceGateError("devicectl app result has no apps list")
    return [item for item in apps if isinstance(item, dict)]


def evaluate_app(
    apps: list[dict[str, Any]],
    *,
    bundle_id: str,
    version: str,
    build: str,
    distribution: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    exact = [item for item in apps if item.get("bundleIdentifier") == bundle_id]
    if len(exact) != 1:
        raise DeviceGateError(f"expected one installed {bundle_id}, found {len(exact)}")
    app = exact[0]
    if str(app.get("version")) != version or str(app.get("bundleVersion")) != build:
        raise DeviceGateError(
            f"installed app is version={app.get('version')!r} build={app.get('bundleVersion')!r}; "
            f"expected version={version!r} build={build!r}"
        )
    expected_developer = {"direct": True, "testflight": False, "any": None}[distribution]
    if expected_developer is not None and app.get("builtByDeveloper") is not expected_developer:
        raise DeviceGateError(
            f"installed app builtByDeveloper={app.get('builtByDeveloper')!r}; "
            f"expected {expected_developer!r} for {distribution}"
        )
    runners = [item for item in apps if is_test_runner(item, bundle_id)]
    return app, runners


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify one exact physical-device app and remove only its XCTest runner shells."
    )
    parser.add_argument("--device", required=True, help="CoreDevice identifier or UDID")
    parser.add_argument("--bundle", required=True, help="canonical product bundle identifier")
    parser.add_argument("--version", required=True, help="expected CFBundleShortVersionString")
    parser.add_argument("--build", required=True, help="expected CFBundleVersion")
    parser.add_argument(
        "--distribution",
        choices=("direct", "testflight", "any"),
        default="any",
        help="expected signing/install origin",
    )
    parser.add_argument(
        "--clean-test-runners",
        action="store_true",
        help="uninstall exact same-bundle-family XCTest runner apps",
    )
    parser.add_argument("--no-icon-check", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        with tempfile.TemporaryDirectory(prefix="autoapp-device-hygiene-") as raw_directory:
            directory = Path(raw_directory)
            before = list_apps(directory, args.device, "apps-before.json")
            runners_before = [item for item in before if is_test_runner(item, args.bundle)]
            removed: list[str] = []
            if runners_before and not args.clean_test_runners:
                ids = [str(item.get("bundleIdentifier")) for item in runners_before]
                raise DeviceGateError(
                    f"test runners remain: {ids}; rerun with --clean-test-runners"
                )
            for runner in runners_before:
                runner_id = str(runner["bundleIdentifier"])
                checked(
                    "xcrun",
                    "devicectl",
                    "device",
                    "uninstall",
                    "app",
                    "--device",
                    args.device,
                    runner_id,
                )
                removed.append(runner_id)

            after = list_apps(directory, args.device, "apps-after.json")
            app, runners_after = evaluate_app(
                after,
                bundle_id=args.bundle,
                version=args.version,
                build=args.build,
                distribution=args.distribution,
            )
            if runners_after:
                raise DeviceGateError(
                    "test runners remain after cleanup: "
                    + repr([item.get("bundleIdentifier") for item in runners_after])
                )

            icon: dict[str, Any] | None = None
            if not args.no_icon_check:
                icon_path = directory / "app-icon.png"
                checked(
                    "xcrun",
                    "devicectl",
                    "device",
                    "info",
                    "appIcon",
                    "--device",
                    args.device,
                    "--app-bundle-id",
                    args.bundle,
                    "--destination",
                    str(icon_path),
                )
                data = icon_path.read_bytes()
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise DeviceGateError("device returned no real PNG app icon")
                icon = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

            receipt = {
                "gate": "autoapp-toolkit/device_app_hygiene.py",
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "device": args.device,
                "bundle_id": args.bundle,
                "version": str(app.get("version")),
                "build": str(app.get("bundleVersion")),
                "built_by_developer": app.get("builtByDeveloper"),
                "distribution": args.distribution,
                "test_runners_before": [item.get("bundleIdentifier") for item in runners_before],
                "test_runners_removed": removed,
                "test_runners_after": [],
                "icon": icon,
            }
            if args.receipt:
                atomic_write(args.receipt, receipt)
    except (DeviceGateError, OSError, KeyError) as error:
        print(f"DEVICE_APP_HYGIENE_FAILED: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        icon_state = "real" if receipt["icon"] else "skipped"
        print(
            "DEVICE_APP_HYGIENE_OK "
            f"bundle={args.bundle} version={args.version} build={args.build} "
            f"built_by_developer={receipt['built_by_developer']} "
            f"runners_removed={len(receipt['test_runners_removed'])} icon={icon_state}"
        )
        if args.receipt:
            print(f"receipt={args.receipt.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
