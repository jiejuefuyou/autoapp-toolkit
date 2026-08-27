#!/usr/bin/env python3
"""Build and optionally upload one exact iOS release candidate.

This local release path deliberately keeps four states separate:

* the tested and remotely readable source commit;
* the signed archive and verified IPA;
* the exact processed App Store Connect build;
* physical-device and App Review evidence, which this tool never claims.

Credentials are read from the private per-user ``~/.appstoreconnect`` directory
and local signing keychain. They are never copied into a receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import asc_monitor


DEFAULT_TEAM_ID = "ZJ3FQ63UV3"
DEFAULT_CERTIFICATE_SHA1 = "F4351C09F287B5AC70146808E5CAA40FC2FE4AF6"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
BUILD_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
BUNDLE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$")


class ReleaseError(RuntimeError):
    """A release condition could not be proved."""


@dataclass(frozen=True)
class ReleaseSpec:
    repo: Path
    project: Path
    scheme: str
    app_name: str
    bundle_id: str
    version: str
    build: str
    git_sha: str
    team_id: str
    remote: str
    remote_branch: str
    output_dir: Path
    receipt: Path


@dataclass(frozen=True)
class ASCCredentials:
    issuer_id: str
    key_id: str
    key_path: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fail(message: str) -> None:
    raise ReleaseError(message)


def validate_identity(
    *, bundle_id: str, version: str, build: str, git_sha: str, team_id: str
) -> None:
    if not BUNDLE_PATTERN.fullmatch(bundle_id) or "." not in bundle_id:
        fail(f"invalid bundle identifier: {bundle_id!r}")
    if not VERSION_PATTERN.fullmatch(version):
        fail(f"version must be x.y.z: {version!r}")
    if not BUILD_PATTERN.fullmatch(build):
        fail(f"build must contain only digits and dots: {build!r}")
    if not SHA_PATTERN.fullmatch(git_sha):
        fail("git SHA must be exactly 40 lowercase hexadecimal characters")
    if not re.fullmatch(r"[A-Z0-9]{10}", team_id):
        fail(f"invalid Apple team identifier: {team_id!r}")


def resolved_inside(root: Path, candidate: Path, description: str) -> Path:
    resolved_root = root.expanduser().resolve()
    resolved_candidate = candidate.expanduser()
    if not resolved_candidate.is_absolute():
        resolved_candidate = resolved_root / resolved_candidate
    resolved_candidate = resolved_candidate.resolve()
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        fail(f"{description} must remain inside repository {resolved_root}")
    return resolved_candidate


def sanitized_detail(text: str, sensitive_values: Iterable[str] = ()) -> str:
    detail = " ".join(text.split())
    for value in sensitive_values:
        if value:
            detail = detail.replace(value, "<redacted>")
    return detail[-2_000:]


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 3_600,
    input_bytes: bytes | None = None,
    sensitive_values: Iterable[str] = (),
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        fail(f"cannot run {command[0]}: {type(error).__name__}: {error}")
    if result.returncode != 0:
        combined = result.stdout.decode("utf-8", "replace") + "\n" + result.stderr.decode(
            "utf-8", "replace"
        )
        detail = sanitized_detail(combined, sensitive_values)
        fail(f"{Path(command[0]).name} failed with exit {result.returncode}: {detail}")
    return result


def command_text(command: list[str], *, cwd: Path, timeout: int = 120) -> str:
    result = run_command(command, cwd=cwd, timeout=timeout)
    return result.stdout.decode("utf-8", "replace").strip()


def load_asc_credentials() -> ASCCredentials:
    issuer_id, key_id, key_path = asc_monitor.load_credentials()
    return ASCCredentials(issuer_id=issuer_id, key_id=key_id, key_path=key_path)


def git_contract(spec: ReleaseSpec) -> dict[str, Any]:
    print("[release] fetch and prove exact remote source", flush=True)
    run_command(["git", "fetch", "--prune", spec.remote], cwd=spec.repo, timeout=300)
    branch = command_text(["git", "branch", "--show-current"], cwd=spec.repo)
    local_sha = command_text(["git", "rev-parse", "HEAD"], cwd=spec.repo)
    origin_sha = command_text(
        ["git", "rev-parse", f"{spec.remote}/{spec.remote_branch}"], cwd=spec.repo
    )
    remote_line = command_text(
        ["git", "ls-remote", spec.remote, f"refs/heads/{spec.remote_branch}"],
        cwd=spec.repo,
        timeout=300,
    )
    remote_sha = remote_line.split()[0] if remote_line else ""
    dirty = command_text(["git", "status", "--porcelain=v1"], cwd=spec.repo)
    counts = command_text(
        ["git", "rev-list", "--left-right", "--count", f"HEAD...{spec.remote}/{spec.remote_branch}"],
        cwd=spec.repo,
    ).split()

    if branch != spec.remote_branch:
        fail(f"release branch must be {spec.remote_branch!r}, got {branch!r}")
    if dirty:
        fail("release worktree is not clean")
    if len(counts) != 2 or counts != ["0", "0"]:
        fail(f"source diverged from {spec.remote}/{spec.remote_branch}: {counts}")
    if len({local_sha, origin_sha, remote_sha, spec.git_sha}) != 1:
        fail(
            "source identity mismatch: "
            f"expected={spec.git_sha} local={local_sha} tracking={origin_sha} remote={remote_sha}"
        )
    return {
        "branch": branch,
        "git_sha": local_sha,
        "tracking_sha": origin_sha,
        "remote_sha": remote_sha,
        "ahead": 0,
        "behind": 0,
        "clean": True,
    }


def toolkit_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    try:
        return {
            "path": str(root),
            "git_sha": command_text(["git", "rev-parse", "HEAD"], cwd=root),
            "clean": not bool(command_text(["git", "status", "--porcelain=v1"], cwd=root)),
        }
    except ReleaseError:
        return {"path": str(root), "git_sha": None, "clean": False}


def prepare_signing_keychain(
    spec: ReleaseSpec, keychain: Path, certificate_sha1: str, password: str
) -> dict[str, Any]:
    print("[release] unlock and probe local distribution signing identity", flush=True)
    keychain = keychain.expanduser().resolve()
    if not keychain.is_file():
        fail(f"local signing keychain is missing: {keychain}")
    secrets = (password,)
    run_command(
        ["security", "unlock-keychain", "-p", password, str(keychain)],
        cwd=spec.repo,
        sensitive_values=secrets,
    )
    run_command(
        [
            "security",
            "set-key-partition-list",
            "-S",
            "apple-tool:,apple:",
            "-s",
            "-k",
            password,
            str(keychain),
        ],
        cwd=spec.repo,
        sensitive_values=secrets,
    )
    identities = command_text(
        ["security", "find-identity", "-v", "-p", "codesigning", str(keychain)],
        cwd=spec.repo,
    )
    if certificate_sha1 not in identities:
        fail(f"distribution identity {certificate_sha1} is missing from {keychain}")

    with tempfile.TemporaryDirectory(prefix="autoapp-signing-probe-") as directory:
        probe = Path(directory) / "probe"
        probe.write_bytes(b"#!/bin/sh\nexit 0\n")
        probe.chmod(0o700)
        run_command(
            ["codesign", "--force", "--sign", certificate_sha1, str(probe)], cwd=spec.repo
        )
        run_command(["codesign", "--verify", "--strict", str(probe)], cwd=spec.repo)
    return {"keychain": str(keychain), "certificate_sha1": certificate_sha1, "probe": "passed"}


def auth_arguments(credentials: ASCCredentials) -> list[str]:
    return [
        "-allowProvisioningUpdates",
        "-authenticationKeyPath",
        str(credentials.key_path),
        "-authenticationKeyID",
        credentials.key_id,
        "-authenticationKeyIssuerID",
        credentials.issuer_id,
    ]


def archive_properties(archive_path: Path) -> dict[str, str]:
    info_path = archive_path / "Info.plist"
    try:
        with info_path.open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as error:
        fail(f"cannot read archive Info.plist: {error}")
    properties = info.get("ApplicationProperties")
    if not isinstance(properties, dict):
        fail("archive has no ApplicationProperties dictionary")
    return {str(key): str(value) for key, value in properties.items()}


def prove_archive_identity(spec: ReleaseSpec, archive_path: Path) -> dict[str, Any]:
    properties = archive_properties(archive_path)
    actual = {
        "bundle_id": properties.get("CFBundleIdentifier"),
        "version": properties.get("CFBundleShortVersionString"),
        "build": properties.get("CFBundleVersion"),
    }
    expected = {"bundle_id": spec.bundle_id, "version": spec.version, "build": spec.build}
    if actual != expected:
        fail(f"archive identity mismatch: expected={expected} actual={actual}")
    return actual


def build_archive(
    spec: ReleaseSpec,
    credentials: ASCCredentials,
    *,
    reuse_artifacts: bool,
) -> Path:
    archive_path = spec.output_dir / (
        f"{spec.app_name}-{spec.version}-build-{spec.build}-{spec.git_sha[:12]}.xcarchive"
    )
    if archive_path.exists():
        if not reuse_artifacts:
            fail(f"archive already exists; pass --reuse-artifacts after inspecting it: {archive_path}")
        print(f"[release] reuse exact archive {archive_path}", flush=True)
        prove_archive_identity(spec, archive_path)
        return archive_path

    print(f"[release] archive {spec.app_name} {spec.version} ({spec.build})", flush=True)
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "xcodebuild",
        "archive",
        "-quiet",
        "-project",
        str(spec.project),
        "-scheme",
        spec.scheme,
        "-configuration",
        "Release",
        "-destination",
        "generic/platform=iOS",
        "-archivePath",
        str(archive_path),
        f"DEVELOPMENT_TEAM={spec.team_id}",
        "CODE_SIGN_STYLE=Automatic",
        *auth_arguments(credentials),
    ]
    run_command(command, cwd=spec.repo, timeout=3_600)
    prove_archive_identity(spec, archive_path)
    return archive_path


def export_archive(
    spec: ReleaseSpec,
    archive_path: Path,
    credentials: ASCCredentials,
    *,
    reuse_artifacts: bool,
) -> Path:
    export_path = spec.output_dir / (
        f"{spec.app_name}-{spec.version}-build-{spec.build}-{spec.git_sha[:12]}-export"
    )
    existing_ipas = sorted(export_path.glob("*.ipa")) if export_path.is_dir() else []
    if existing_ipas:
        if len(existing_ipas) != 1:
            fail(f"expected one existing IPA in {export_path}, found {len(existing_ipas)}")
        if not reuse_artifacts:
            fail(f"export already exists; pass --reuse-artifacts after inspecting it: {export_path}")
        print(f"[release] reuse exact IPA {existing_ipas[0]}", flush=True)
        return existing_ipas[0]
    if export_path.exists():
        fail(f"partial export path exists without one IPA: {export_path}")

    print("[release] export App Store Connect IPA", flush=True)
    with tempfile.TemporaryDirectory(prefix="autoapp-export-options-") as directory:
        options_path = Path(directory) / "ExportOptions.plist"
        with options_path.open("wb") as handle:
            plistlib.dump(
                {
                    "destination": "export",
                    "manageAppVersionAndBuildNumber": False,
                    "method": "app-store-connect",
                    "signingStyle": "automatic",
                    "teamID": spec.team_id,
                    "uploadSymbols": True,
                },
                handle,
            )
        command = [
            "xcodebuild",
            "-exportArchive",
            "-quiet",
            "-archivePath",
            str(archive_path),
            "-exportPath",
            str(export_path),
            "-exportOptionsPlist",
            str(options_path),
            *auth_arguments(credentials),
        ]
        run_command(command, cwd=spec.repo, timeout=3_600)

    ipas = sorted(export_path.glob("*.ipa"))
    if len(ipas) != 1:
        fail(f"expected one exported IPA in {export_path}, found {len(ipas)}")
    return ipas[0]


def safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    for member in archive.infolist():
        member_path = Path(member.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            fail(f"unsafe IPA member path: {member.filename!r}")
    archive.extractall(destination)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_ipa(
    spec: ReleaseSpec, ipa_path: Path, expected_certificate_sha1: str
) -> dict[str, Any]:
    print("[release] verify IPA identity, signature, and distribution profile", flush=True)
    with tempfile.TemporaryDirectory(prefix="autoapp-ipa-inspect-") as directory:
        destination = Path(directory)
        try:
            with zipfile.ZipFile(ipa_path) as archive:
                safe_extract(archive, destination)
        except (OSError, zipfile.BadZipFile) as error:
            fail(f"cannot inspect IPA {ipa_path}: {error}")
        apps = sorted((destination / "Payload").glob("*.app"))
        if len(apps) != 1:
            fail(f"expected one root app in IPA, found {len(apps)}")
        app_path = apps[0]
        run_command(["codesign", "--verify", "--deep", "--strict", str(app_path)], cwd=spec.repo)

        certificate_prefix = destination / "signing-certificate"
        run_command(
            [
                "codesign",
                "--display",
                f"--extract-certificates={certificate_prefix}",
                str(app_path),
            ],
            cwd=spec.repo,
        )
        leaf_certificate = Path(f"{certificate_prefix}0")
        if not leaf_certificate.is_file():
            fail("codesign did not extract the IPA leaf signing certificate")
        certificate_sha1 = hashlib.sha1(leaf_certificate.read_bytes()).hexdigest().upper()
        if certificate_sha1 != expected_certificate_sha1:
            fail(
                "IPA signing certificate mismatch: "
                f"expected={expected_certificate_sha1} actual={certificate_sha1}"
            )

        try:
            with (app_path / "Info.plist").open("rb") as handle:
                info = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException) as error:
            fail(f"cannot read IPA Info.plist: {error}")
        identity = {
            "bundle_id": str(info.get("CFBundleIdentifier", "")),
            "version": str(info.get("CFBundleShortVersionString", "")),
            "build": str(info.get("CFBundleVersion", "")),
        }
        expected = {"bundle_id": spec.bundle_id, "version": spec.version, "build": spec.build}
        if identity != expected:
            fail(f"IPA identity mismatch: expected={expected} actual={identity}")
        if info.get("ITSAppUsesNonExemptEncryption") is not False:
            fail("IPA must declare ITSAppUsesNonExemptEncryption=false")

        profile_path = app_path / "embedded.mobileprovision"
        profile_result = run_command(
            ["security", "cms", "-D", "-i", str(profile_path)], cwd=spec.repo
        )
        try:
            profile = plistlib.loads(profile_result.stdout)
        except plistlib.InvalidFileException as error:
            fail(f"cannot parse embedded provisioning profile: {error}")
        entitlements = profile.get("Entitlements")
        if not isinstance(entitlements, dict):
            fail("embedded provisioning profile has no entitlements")
        profile_team = str(entitlements.get("com.apple.developer.team-identifier", ""))
        profile_app = str(entitlements.get("application-identifier", ""))
        if profile_team != spec.team_id:
            fail(f"profile team mismatch: expected={spec.team_id} actual={profile_team}")
        if profile_app != f"{spec.team_id}.{spec.bundle_id}":
            fail(f"profile application identifier mismatch: {profile_app}")
        if profile.get("ProvisionsAllDevices") is True or profile.get("ProvisionedDevices"):
            fail("IPA contains a device-scoped profile instead of App Store distribution")
        if entitlements.get("beta-reports-active") is not True:
            fail("App Store distribution profile is missing beta-reports-active=true")
        expiration = profile.get("ExpirationDate")
        if not isinstance(expiration, datetime):
            fail("provisioning profile has no parseable expiration date")
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)
        if expiration <= datetime.now(timezone.utc):
            fail(f"provisioning profile expired at {expiration.isoformat()}")

        return {
            **identity,
            "ipa_path": str(ipa_path),
            "ipa_sha256": sha256_file(ipa_path),
            "ipa_size": ipa_path.stat().st_size,
            "codesign": "passed",
            "signing_certificate_sha1": certificate_sha1,
            "encryption_declaration": False,
            "profile_name": profile.get("Name"),
            "profile_uuid": profile.get("UUID"),
            "profile_expiration": expiration.isoformat(),
            "profile_team_id": profile_team,
            "profile_application_identifier": profile_app,
            "beta_reports_active": entitlements.get("beta-reports-active"),
        }


def exact_asc_build(spec: ReleaseSpec, credentials: ASCCredentials) -> dict[str, Any] | None:
    token = asc_monitor.mint_token(
        credentials.issuer_id, credentials.key_id, credentials.key_path
    )
    apps = asc_monitor.matching_apps(token, bundle_id=spec.bundle_id, name_filter=None)
    app_id = str(apps[0]["id"])
    payload = asc_monitor.api_get(
        token,
        "/v1/builds",
        {
            "filter[app]": app_id,
            "filter[version]": spec.build,
            "include": "preReleaseVersion",
            "limit": 200,
        },
    )
    included = asc_monitor.included_index(payload)
    matches: list[dict[str, Any]] = []
    for resource in payload.get("data", []):
        attributes = resource.get("attributes", {})
        train_id = asc_monitor.relationship_id(resource, "preReleaseVersion")
        train = included.get(("preReleaseVersions", train_id or ""), {})
        train_attributes = train.get("attributes", {})
        if str(attributes.get("version", "")) != spec.build:
            continue
        if str(train_attributes.get("version", "")) != spec.version:
            continue
        matches.append(
            {
                "id": resource.get("id"),
                "build": attributes.get("version"),
                "train": train_attributes.get("version"),
                "platform": train_attributes.get("platform"),
                "processing_state": attributes.get("processingState"),
                "uploaded_date": attributes.get("uploadedDate"),
                "expired": attributes.get("expired"),
                "expiration_date": attributes.get("expirationDate"),
                "minimum_os": attributes.get("minOsVersion"),
                "uses_non_exempt_encryption": attributes.get("usesNonExemptEncryption"),
                "audience": attributes.get("buildAudienceType"),
                "has_icon": bool(
                    isinstance(attributes.get("iconAssetToken"), dict)
                    and attributes.get("iconAssetToken", {}).get("templateUrl")
                ),
            }
        )
    if len(matches) > 1:
        fail(
            f"ASC returned {len(matches)} builds for exact train/build "
            f"{spec.version}/{spec.build}"
        )
    return matches[0] if matches else None


def asc_build_checks(build: dict[str, Any]) -> list[dict[str, Any]]:
    expiration_text = str(build.get("expiration_date") or "")
    try:
        expiration = datetime.fromisoformat(expiration_text.replace("Z", "+00:00"))
    except ValueError:
        expiration = None
    now = datetime.now(timezone.utc)
    return [
        {"name": "processing_valid", "ok": build.get("processing_state") == "VALID"},
        {"name": "ios_platform", "ok": build.get("platform") == "IOS"},
        {"name": "not_expired", "ok": build.get("expired") is False},
        {
            "name": "future_expiration",
            "ok": expiration is not None and expiration > now,
        },
        {"name": "minimum_os_declared", "ok": bool(build.get("minimum_os"))},
        {"name": "real_app_icon", "ok": build.get("has_icon") is True},
        {
            "name": "encryption_resolved",
            "ok": build.get("uses_non_exempt_encryption") is False,
        },
        {
            "name": "app_store_eligible",
            "ok": build.get("audience") in {"APP_STORE_ELIGIBLE", "INTERNAL_ONLY"},
        },
    ]


def upload_ipa(
    spec: ReleaseSpec,
    credentials: ASCCredentials,
    ipa_path: Path,
    *,
    wait_timeout: int,
    wait_interval: int,
) -> dict[str, Any]:
    existing = exact_asc_build(spec, credentials)
    if existing is not None:
        fail(
            f"ASC already contains exact train/build {spec.version}/{spec.build} "
            f"state={existing.get('processing_state')} id={existing.get('id')}; refusing duplicate upload"
        )

    print(f"[release] upload exact IPA to TestFlight train {spec.version} build {spec.build}", flush=True)
    upload = run_command(
        [
            "xcrun",
            "altool",
            "--upload-app",
            "--type",
            "ios",
            "--file",
            str(ipa_path),
            "--apiKey",
            credentials.key_id,
            "--apiIssuer",
            credentials.issuer_id,
        ],
        cwd=spec.repo,
        timeout=3_600,
    )
    output = upload.stdout.decode("utf-8", "replace") + upload.stderr.decode(
        "utf-8", "replace"
    )
    request_match = re.search(
        r"(?:RequestUUID|Request ID|Delivery UUID)[^0-9A-Fa-f]*([0-9A-Fa-f-]{36})", output
    )
    request_id = request_match.group(1) if request_match else None

    deadline = time.monotonic() + wait_timeout
    last_state: tuple[Any, ...] | None = None
    while time.monotonic() < deadline:
        build = exact_asc_build(spec, credentials)
        if build is None:
            state = ("not-visible",)
        else:
            checks = asc_build_checks(build)
            state = (
                build.get("processing_state"),
                build.get("has_icon"),
                build.get("uses_non_exempt_encryption"),
            )
            if all(item["ok"] for item in checks):
                build["checks"] = checks
                build["request_id"] = request_id
                print(
                    "[release] ASC exact build processed "
                    f"id={build.get('id')} train={spec.version} build={spec.build}",
                    flush=True,
                )
                return build
            if build.get("processing_state") in {"FAILED", "INVALID"}:
                failed = [item["name"] for item in checks if not item["ok"]]
                fail(
                    f"ASC rejected exact build {spec.version}/{spec.build}: "
                    f"state={build.get('processing_state')} failed_checks={failed}"
                )
        if state != last_state:
            print(f"[release] ASC wait state={state}", flush=True)
            last_state = state
        time.sleep(wait_interval)
    fail(
        f"timed out after {wait_timeout}s waiting for exact ASC build "
        f"{spec.version}/{spec.build} to become fully valid"
    )


def base_receipt(
    spec: ReleaseSpec,
    *,
    source: dict[str, Any],
    signing: dict[str, Any],
    archive_path: Path,
    ipa: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "gate": "autoapp-toolkit/local_testflight_release.py",
        "stage": "artifact-verified",
        "observed_at": utc_now(),
        "app": {
            "name": spec.app_name,
            "bundle_id": spec.bundle_id,
            "version": spec.version,
            "build": spec.build,
        },
        "source": source,
        "toolkit": toolkit_identity(),
        "signing": signing,
        "artifact": {
            "archive_path": str(archive_path),
            **ipa,
        },
        "testflight": None,
        "claims": {
            "source_remote_readback": True,
            "signed_artifact_verified": True,
            "testflight_processed": False,
            "testflight_distributed_to_testers": False,
            "physical_device_installed": False,
            "account_holder_business_ready": False,
            "app_review_submitted": False,
        },
        "manual_gates_not_proved": [
            "internal tester group and accepted App Store Connect user",
            "exact build installed and accepted on a physical device",
            "Paid Apps agreement, tax, and banking status",
            "exact app version and required IAP in one live App Review submission",
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail-closed local archive/export and optional exact TestFlight upload."
    )
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--scheme", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--team-id", default=DEFAULT_TEAM_ID)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--remote-branch", default="main")
    parser.add_argument("--output-dir", type=Path, default=Path(".verify/release"))
    parser.add_argument("--receipt", type=Path)
    parser.add_argument(
        "--keychain",
        type=Path,
        default=Path.home() / "Library" / "Keychains" / "rr-build.keychain-db",
    )
    parser.add_argument("--certificate-sha1", default=DEFAULT_CERTIFICATE_SHA1)
    parser.add_argument(
        "--reuse-artifacts",
        action="store_true",
        help="reuse only after re-verifying an existing exact archive and IPA",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="perform the external TestFlight upload; omitted means artifact-only",
    )
    parser.add_argument("--wait-timeout", type=int, default=1_800)
    parser.add_argument("--wait-interval", type=int, default=30)
    return parser.parse_args(argv)


def make_spec(args: argparse.Namespace) -> ReleaseSpec:
    repo = args.repo.expanduser().resolve()
    if not (repo / ".git").exists():
        fail(f"repository has no .git directory: {repo}")
    git_sha = str(args.git_sha).lower()
    validate_identity(
        bundle_id=args.bundle,
        version=args.version,
        build=args.build,
        git_sha=git_sha,
        team_id=args.team_id,
    )
    if not re.fullmatch(r"[A-Za-z0-9_. -]+", args.app_name):
        fail(f"app name contains unsupported characters: {args.app_name!r}")
    if not re.fullmatch(r"[A-Za-z0-9_. -]+", args.scheme):
        fail(f"scheme contains unsupported characters: {args.scheme!r}")
    project = resolved_inside(repo, args.project, "project")
    if not project.is_dir() or project.suffix != ".xcodeproj":
        fail(f"Xcode project is missing: {project}")
    output_dir = resolved_inside(repo, args.output_dir, "output directory")
    receipt_argument = args.receipt or (
        output_dir / f"{args.app_name}-{args.version}-build-{args.build}-{git_sha[:12]}.json"
    )
    receipt = resolved_inside(repo, receipt_argument, "receipt")
    return ReleaseSpec(
        repo=repo,
        project=project,
        scheme=args.scheme,
        app_name=args.app_name,
        bundle_id=args.bundle,
        version=args.version,
        build=args.build,
        git_sha=git_sha,
        team_id=args.team_id,
        remote=args.remote,
        remote_branch=args.remote_branch,
        output_dir=output_dir,
        receipt=receipt,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not 60 <= args.wait_timeout <= 7_200:
            fail("--wait-timeout must be between 60 and 7200 seconds")
        if not 5 <= args.wait_interval <= 300:
            fail("--wait-interval must be between 5 and 300 seconds")
        spec = make_spec(args)
        credentials = load_asc_credentials()
        source = git_contract(spec)
        signing = prepare_signing_keychain(
            spec,
            args.keychain,
            args.certificate_sha1.upper(),
            os.environ.get("LOCAL_SIGNING_KEYCHAIN_PASSWORD", ""),
        )
        archive_path = build_archive(
            spec, credentials, reuse_artifacts=args.reuse_artifacts
        )
        ipa_path = export_archive(
            spec,
            archive_path,
            credentials,
            reuse_artifacts=args.reuse_artifacts,
        )
        ipa = verify_ipa(spec, ipa_path, args.certificate_sha1.upper())
        receipt = base_receipt(
            spec,
            source=source,
            signing=signing,
            archive_path=archive_path,
            ipa=ipa,
        )
        asc_monitor.write_snapshot(spec.receipt, receipt)
        print(f"[release] artifact receipt={spec.receipt}", flush=True)

        if args.upload:
            build = upload_ipa(
                spec,
                credentials,
                ipa_path,
                wait_timeout=args.wait_timeout,
                wait_interval=args.wait_interval,
            )
            receipt["stage"] = "testflight-processed"
            receipt["observed_at"] = utc_now()
            receipt["testflight"] = build
            receipt["claims"]["testflight_processed"] = True
            asc_monitor.write_snapshot(spec.receipt, receipt)
            print(
                "TESTFLIGHT_EXACT_BUILD_OK "
                f"bundle={spec.bundle_id} train={spec.version} build={spec.build} "
                f"git_sha={spec.git_sha} asc_build_id={build.get('id')} "
                "device_install=unproven app_review=unsubmitted",
                flush=True,
            )
        else:
            print(
                "RELEASE_ARTIFACT_OK "
                f"bundle={spec.bundle_id} version={spec.version} build={spec.build} "
                f"git_sha={spec.git_sha} upload=not-requested",
                flush=True,
            )
        return 0
    except (ReleaseError, asc_monitor.ASCError, OSError) as error:
        print(f"LOCAL_TESTFLIGHT_RELEASE_FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
