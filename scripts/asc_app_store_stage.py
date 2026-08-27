#!/usr/bin/env python3
"""Stage one exact processed build as an App Store version without submitting it.

The tool is intentionally idempotent and split from App Review submission. It
binds an exact TestFlight receipt to one draft version, copies version metadata
from the newest live version when needed, writes localized release notes, and
then performs a read-only completeness gate. It never submits the draft.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asc_monitor


RELEASE_NOTES = {
    "en-US": "New features, reliability improvements, and a more polished experience.",
    "ja": "新機能、信頼性の向上、使い勝手の改善を行いました。",
    "zh-Hans": "新增功能，并提升了可靠性与使用体验。",
    "zh-Hant": "新增功能，並提升了可靠性與使用體驗。",
    "ko": "새로운 기능을 추가하고 안정성과 사용성을 개선했습니다.",
    "es-ES": "Nuevas funciones y mejoras de fiabilidad y experiencia de uso.",
    "fr-FR": "Nouvelles fonctionnalités et améliorations de la fiabilité et de l’expérience.",
    "de-DE": "Neue Funktionen sowie Verbesserungen bei Zuverlässigkeit und Bedienung.",
}
EDITABLE_STATES = {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED"}
IPHONE_SCREENSHOT_TYPES = {"APP_IPHONE_65", "APP_IPHONE_67"}
IPAD_SCREENSHOT_TYPES = {
    "APP_IPAD_PRO_3GEN_129",
    "APP_IPAD_PRO_129",
    "APP_IPAD_PRO_13",
}


class StageError(RuntimeError):
    """An App Store staging condition could not be proved."""


@dataclass(frozen=True)
class StageSpec:
    bundle_id: str
    version: str
    build: str
    build_id: str
    requires_ipad: bool
    release_receipt: Path
    receipt: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fail(message: str) -> None:
    raise StageError(message)


def api_write(
    token: str,
    method: str,
    path: str,
    body: dict[str, Any],
) -> dict[str, Any] | None:
    if method not in {"POST", "PATCH"}:
        raise ValueError(f"unsupported ASC write method: {method}")
    request = urllib.request.Request(
        asc_monitor.api_url(path),
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "autoapp-toolkit-asc-stage/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
            detail = "; ".join(
                f"{item.get('code', '?')}: {item.get('detail', item.get('title', ''))}"
                for item in payload.get("errors", [])
            )
        except json.JSONDecodeError:
            detail = " ".join(raw.split())[:800]
        raise StageError(f"{method} {path} returned {error.code}: {detail}") from error
    except (OSError, TimeoutError) as error:
        raise StageError(
            f"{method} {path} failed: {type(error).__name__}: {error}"
        ) from error
    if not data:
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as error:
        raise StageError(f"{method} {path} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise StageError(f"{method} {path} returned a non-object payload")
    return payload


def semantic_version(value: Any) -> tuple[int, ...]:
    text = str(value or "")
    try:
        return tuple(int(part) for part in text.split("."))
    except ValueError as error:
        raise StageError(f"ASC returned non-numeric app version {text!r}") from error


def exact_one(resources: list[dict[str, Any]], description: str) -> dict[str, Any]:
    if len(resources) != 1:
        fail(f"expected exactly one {description}, found {len(resources)}")
    return resources[0]


def load_release_receipt(spec: StageSpec) -> dict[str, Any]:
    try:
        payload = json.loads(spec.release_receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot read exact TestFlight receipt {spec.release_receipt}: {error}")
    expected_app = {
        "bundle_id": spec.bundle_id,
        "version": spec.version,
        "build": spec.build,
    }
    app = payload.get("app", {})
    actual_app = {key: app.get(key) for key in expected_app}
    if actual_app != expected_app:
        fail(f"TestFlight receipt app mismatch: expected={expected_app} actual={actual_app}")
    if payload.get("stage") != "testflight-processed":
        fail(f"TestFlight receipt stage is {payload.get('stage')!r}, not processed")
    if payload.get("claims", {}).get("testflight_processed") is not True:
        fail("TestFlight receipt does not claim exact build processing")
    if payload.get("testflight", {}).get("id") != spec.build_id:
        fail(
            "TestFlight receipt ASC build id mismatch: "
            f"expected={spec.build_id} actual={payload.get('testflight', {}).get('id')}"
        )
    if payload.get("source", {}).get("clean") is not True:
        fail("TestFlight receipt source was not clean")
    remote_sha = payload.get("source", {}).get("remote_sha")
    git_sha = payload.get("source", {}).get("git_sha")
    if not remote_sha or remote_sha != git_sha:
        fail("TestFlight receipt lacks exact remote source readback")
    return payload


def read_app(token: str, bundle_id: str) -> dict[str, Any]:
    return exact_one(
        asc_monitor.matching_apps(token, bundle_id=bundle_id, name_filter=None),
        f"app {bundle_id}",
    )


def read_versions(token: str, app_id: str) -> list[dict[str, Any]]:
    return asc_monitor.api_get(
        token,
        f"/v1/apps/{app_id}/appStoreVersions",
        {"filter[platform]": "IOS", "limit": 200},
    ).get("data", [])


def version_state(resource: dict[str, Any]) -> str:
    return asc_monitor.state_of(resource.get("attributes", {}))


def latest_live_version(versions: list[dict[str, Any]]) -> dict[str, Any]:
    live = [item for item in versions if version_state(item) == "READY_FOR_SALE"]
    if not live:
        fail("app has no READY_FOR_SALE iOS version to use as metadata source")
    return max(
        live,
        key=lambda item: semantic_version(item.get("attributes", {}).get("versionString")),
    )


def target_version(
    versions: list[dict[str, Any]], version: str
) -> dict[str, Any] | None:
    matches = [
        item
        for item in versions
        if str(item.get("attributes", {}).get("versionString")) == version
    ]
    if len(matches) > 1:
        fail(f"ASC returned multiple iOS App Store versions for {version}")
    return matches[0] if matches else None


def read_exact_build(token: str, spec: StageSpec, app_id: str) -> dict[str, Any]:
    payload = asc_monitor.api_get(
        token,
        f"/v1/builds/{spec.build_id}",
        {"include": "app,preReleaseVersion"},
    )
    resource = payload.get("data")
    if not isinstance(resource, dict):
        fail(f"ASC build id {spec.build_id} has no resource")
    included = asc_monitor.included_index(payload)
    app_relationship = asc_monitor.relationship_id(resource, "app")
    train_relationship = asc_monitor.relationship_id(resource, "preReleaseVersion")
    app = included.get(("apps", app_relationship or ""), {})
    train = included.get(("preReleaseVersions", train_relationship or ""), {})
    attributes = resource.get("attributes", {})
    actual = {
        "id": resource.get("id"),
        "bundle_id": app.get("attributes", {}).get("bundleId"),
        "app_id": app_relationship,
        "train": train.get("attributes", {}).get("version"),
        "platform": train.get("attributes", {}).get("platform"),
        "build": attributes.get("version"),
        "processing_state": attributes.get("processingState"),
        "expired": attributes.get("expired"),
        "uses_non_exempt_encryption": attributes.get("usesNonExemptEncryption"),
        "has_icon": bool(
            isinstance(attributes.get("iconAssetToken"), dict)
            and attributes.get("iconAssetToken", {}).get("templateUrl")
        ),
    }
    expected = {
        "bundle_id": spec.bundle_id,
        "app_id": app_id,
        "train": spec.version,
        "platform": "IOS",
        "build": spec.build,
        "processing_state": "VALID",
        "expired": False,
        "uses_non_exempt_encryption": False,
        "has_icon": True,
    }
    for key, value in expected.items():
        if actual.get(key) != value:
            fail(
                f"exact ASC build check failed for {key}: "
                f"expected={value!r} actual={actual.get(key)!r}"
            )
    return actual


def read_localizations(token: str, version_id: str) -> list[dict[str, Any]]:
    return asc_monitor.api_get(
        token,
        f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
        {"limit": 200},
    ).get("data", [])


def copyable_localization_attributes(resource: dict[str, Any]) -> dict[str, Any]:
    source = resource.get("attributes", {})
    attributes: dict[str, Any] = {"locale": source.get("locale")}
    for key in (
        "description",
        "keywords",
        "marketingUrl",
        "promotionalText",
        "supportUrl",
    ):
        value = source.get(key)
        if value is not None:
            attributes[key] = value
    locale = str(source.get("locale") or "")
    attributes["whatsNew"] = RELEASE_NOTES.get(locale, RELEASE_NOTES["en-US"])
    return attributes


def create_version(token: str, app_id: str, spec: StageSpec) -> dict[str, Any]:
    payload = api_write(
        token,
        "POST",
        "/v1/appStoreVersions",
        {
            "data": {
                "type": "appStoreVersions",
                "attributes": {
                    "platform": "IOS",
                    "versionString": spec.version,
                    "releaseType": "MANUAL",
                    "usesIdfa": False,
                },
                "relationships": {
                    "app": {"data": {"type": "apps", "id": app_id}}
                },
            }
        },
    )
    resource = (payload or {}).get("data")
    if not isinstance(resource, dict):
        fail("ASC version creation returned no version resource")
    return resource


def attach_build(token: str, version_id: str, build_id: str) -> None:
    api_write(
        token,
        "PATCH",
        f"/v1/appStoreVersions/{version_id}/relationships/build",
        {"data": {"type": "builds", "id": build_id}},
    )


def upsert_localizations(
    token: str,
    target_version_id: str,
    live_localizations: list[dict[str, Any]],
) -> None:
    target = read_localizations(token, target_version_id)
    target_by_locale = {
        str(item.get("attributes", {}).get("locale")): item for item in target
    }
    for source in live_localizations:
        attributes = copyable_localization_attributes(source)
        locale = str(attributes["locale"])
        existing = target_by_locale.get(locale)
        if existing:
            patch_attributes = dict(attributes)
            patch_attributes.pop("locale", None)
            api_write(
                token,
                "PATCH",
                f"/v1/appStoreVersionLocalizations/{existing['id']}",
                {
                    "data": {
                        "type": "appStoreVersionLocalizations",
                        "id": existing["id"],
                        "attributes": patch_attributes,
                    }
                },
            )
        else:
            api_write(
                token,
                "POST",
                "/v1/appStoreVersionLocalizations",
                {
                    "data": {
                        "type": "appStoreVersionLocalizations",
                        "attributes": attributes,
                        "relationships": {
                            "appStoreVersion": {
                                "data": {
                                    "type": "appStoreVersions",
                                    "id": target_version_id,
                                }
                            }
                        },
                    }
                },
            )


def selected_build(token: str, version_id: str) -> dict[str, Any] | None:
    return asc_monitor.read_version_build(token, version_id)


def screenshot_summary(
    token: str, localization_id: str
) -> list[dict[str, Any]]:
    sets = asc_monitor.api_get(
        token,
        f"/v1/appStoreVersionLocalizations/{localization_id}/appScreenshotSets",
        {"limit": 50},
    ).get("data", [])
    rows: list[dict[str, Any]] = []
    for screenshot_set in sets:
        total = (
            screenshot_set.get("relationships", {})
            .get("appScreenshots", {})
            .get("meta", {})
            .get("paging", {})
            .get("total")
        )
        if total is None:
            screenshots = asc_monitor.api_get(
                token,
                f"/v1/appScreenshotSets/{screenshot_set['id']}/appScreenshots",
                {"limit": 50},
            ).get("data", [])
            total = len(screenshots)
        rows.append(
            {
                "display_type": screenshot_set.get("attributes", {}).get(
                    "screenshotDisplayType"
                ),
                "count": int(total or 0),
            }
        )
    return rows


def read_stage_snapshot(
    token: str,
    spec: StageSpec,
    app: dict[str, Any],
    version: dict[str, Any],
    build: dict[str, Any],
    source_sha: str,
) -> dict[str, Any]:
    version_id = str(version["id"])
    attributes = version.get("attributes", {})
    localizations = read_localizations(token, version_id)
    localization_rows: list[dict[str, Any]] = []
    for item in localizations:
        item_attributes = item.get("attributes", {})
        localization_rows.append(
            {
                "id": item.get("id"),
                "locale": item_attributes.get("locale"),
                "description": bool(item_attributes.get("description")),
                "keywords": bool(item_attributes.get("keywords")),
                "support_url": bool(item_attributes.get("supportUrl")),
                "marketing_url": bool(item_attributes.get("marketingUrl")),
                "whats_new": bool(item_attributes.get("whatsNew")),
                "screenshot_sets": screenshot_summary(token, str(item["id"])),
            }
        )
    selected = selected_build(token, version_id)
    en_us = next(
        (item for item in localization_rows if item.get("locale") == "en-US"), None
    )
    all_sets = [
        screenshot
        for localization in localization_rows
        for screenshot in localization["screenshot_sets"]
        if screenshot["count"] > 0
    ]
    iphone_count = sum(
        item["count"] for item in all_sets if item["display_type"] in IPHONE_SCREENSHOT_TYPES
    )
    ipad_count = sum(
        item["count"] for item in all_sets if item["display_type"] in IPAD_SCREENSHOT_TYPES
    )
    checks = [
        {"name": "prepare_for_submission", "ok": version_state(version) in EDITABLE_STATES},
        {"name": "manual_release", "ok": attributes.get("releaseType") == "MANUAL"},
        {
            "name": "exact_build_selected",
            "ok": bool(
                selected
                and selected.get("id") == spec.build_id
                and str(selected.get("build")) == spec.build
                and selected.get("processing_state") == "VALID"
            ),
        },
        {"name": "localizations_present", "ok": bool(localization_rows)},
        {
            "name": "localized_metadata_complete",
            "ok": bool(localization_rows)
            and all(
                item["description"]
                and item["keywords"]
                and item["support_url"]
                and item["whats_new"]
                for item in localization_rows
            ),
        },
        {
            "name": "primary_marketing_url",
            "ok": bool(en_us and en_us["marketing_url"]),
        },
        {"name": "iphone_screenshots", "ok": iphone_count > 0},
        {
            "name": "ipad_screenshots_when_required",
            "ok": not spec.requires_ipad or ipad_count > 0,
        },
    ]
    return {
        "schema_version": 1,
        "gate": "autoapp-toolkit/asc_app_store_stage.py",
        "stage": "app-store-staged" if all(item["ok"] for item in checks) else "app-store-stage-incomplete",
        "observed_at": utc_now(),
        "app": {
            "id": app.get("id"),
            "name": app.get("attributes", {}).get("name"),
            "bundle_id": spec.bundle_id,
        },
        "version": {
            "id": version_id,
            "version": attributes.get("versionString"),
            "state": version_state(version),
            "release_type": attributes.get("releaseType"),
            "selected_build": selected,
        },
        "exact_build": build,
        "source_git_sha": source_sha,
        "localizations": localization_rows,
        "screenshot_counts": {"iphone": iphone_count, "ipad": ipad_count},
        "checks": checks,
        "staged_ok": all(item["ok"] for item in checks),
        "claims": {
            "testflight_processed": True,
            "app_store_version_staged": all(item["ok"] for item in checks),
            "physical_device_installed": False,
            "account_holder_business_ready": False,
            "app_review_submitted": False,
        },
        "manual_gates_not_proved": [
            "exact build installed and accepted on a physical device",
            "Paid Apps agreement, tax, and banking status",
            "live App Review submission containing this exact version and build",
        ],
    }


def validate_spec(args: argparse.Namespace) -> StageSpec:
    if not re.fullmatch(r"[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)+", args.bundle):
        fail(f"invalid bundle identifier: {args.bundle!r}")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        fail(f"version must be x.y.z: {args.version!r}")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", args.build):
        fail(f"invalid build number: {args.build!r}")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", args.build_id):
        fail(f"invalid ASC build id: {args.build_id!r}")
    release_receipt = args.release_receipt.expanduser().resolve()
    receipt = args.receipt.expanduser().resolve()
    return StageSpec(
        bundle_id=args.bundle,
        version=args.version,
        build=args.build,
        build_id=args.build_id,
        requires_ipad=args.requires_ipad,
        release_receipt=release_receipt,
        receipt=receipt,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage one exact App Store version/build without submitting it."
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--release-receipt", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--requires-ipad", action="store_true")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create/update the draft and select the build; omitted is read-only preview",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        spec = validate_spec(args)
        release_receipt = load_release_receipt(spec)
        source_sha = str(release_receipt["source"]["git_sha"])
        issuer_id, key_id, key_path = asc_monitor.load_credentials()
        token = asc_monitor.mint_token(issuer_id, key_id, key_path)
        app = read_app(token, spec.bundle_id)
        app_id = str(app["id"])
        build = read_exact_build(token, spec, app_id)
        versions = read_versions(token, app_id)
        live = latest_live_version(versions)
        target = target_version(versions, spec.version)

        if not args.apply:
            preview = {
                "schema_version": 1,
                "gate": "autoapp-toolkit/asc_app_store_stage.py",
                "stage": "preview",
                "observed_at": utc_now(),
                "app": {"id": app_id, "bundle_id": spec.bundle_id},
                "source_git_sha": source_sha,
                "exact_build": build,
                "latest_live_version": live.get("attributes", {}).get("versionString"),
                "target_version_exists": target is not None,
                "would_create_version": target is None,
                "would_select_build": spec.build_id,
                "would_copy_localizations": sorted(
                    str(item.get("attributes", {}).get("locale"))
                    for item in read_localizations(token, str(live["id"]))
                ),
                "would_submit_for_review": False,
            }
            asc_monitor.write_snapshot(spec.receipt, preview)
            print(
                "ASC_APP_STORE_STAGE_PREVIEW_OK "
                f"bundle={spec.bundle_id} version={spec.version} build={spec.build} "
                f"target_exists={target is not None} submit=false receipt={spec.receipt}"
            )
            return 0

        if target is None:
            print(
                f"[stage] create App Store version {spec.version} with manual release",
                flush=True,
            )
            target = create_version(token, app_id, spec)
        state = version_state(target)
        if state not in EDITABLE_STATES:
            fail(f"target App Store version {spec.version} is not editable: {state}")
        target_id = str(target["id"])

        print(f"[stage] attach exact ASC build id={spec.build_id}", flush=True)
        attach_build(token, target_id, spec.build_id)
        print("[stage] copy live metadata and write localized release notes", flush=True)
        live_localizations = read_localizations(token, str(live["id"]))
        if not live_localizations:
            fail("latest live version has no localizations to copy")
        upsert_localizations(token, target_id, live_localizations)

        refreshed_versions = read_versions(token, app_id)
        refreshed_target = target_version(refreshed_versions, spec.version)
        if refreshed_target is None:
            fail("created App Store version disappeared during readback")
        snapshot = read_stage_snapshot(
            token, spec, app, refreshed_target, build, source_sha
        )
        asc_monitor.write_snapshot(spec.receipt, snapshot)
        failed = [item["name"] for item in snapshot["checks"] if not item["ok"]]
        if failed:
            fail(
                "App Store draft exists but staging is incomplete: " + ", ".join(failed)
            )
        print(
            "ASC_APP_STORE_STAGE_OK "
            f"bundle={spec.bundle_id} version={spec.version} build={spec.build} "
            f"version_id={snapshot['version']['id']} submit=false "
            f"receipt={spec.receipt}"
        )
        return 0
    except (StageError, asc_monitor.ASCError, OSError) as error:
        print(f"ASC_APP_STORE_STAGE_FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
