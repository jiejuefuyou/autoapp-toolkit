#!/usr/bin/env python3
"""Fail-closed, read-only TestFlight internal-distribution server gate.

This gate proves the App Store Connect side of the distribution chain. It does
not claim that a particular iPhone is signed into the Apple Account that
redeemed the invitation, nor that the build is installed on that phone.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asc_monitor


ELIGIBLE_INTERNAL_ROLES = {
    "ACCOUNT_HOLDER",
    "ADMIN",
    "APP_MANAGER",
    "DEVELOPER",
    "MARKETING",
}
ACCEPTED_TESTER_STATES = {"ACCEPTED", "INSTALLED"}


class GateError(RuntimeError):
    """A readiness condition could not be proved."""


def api_get_all(
    token: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read every page while refusing pagination links outside Apple's API."""

    items: list[dict[str, Any]] = []
    next_path = path
    next_params = params
    while next_path:
        payload = asc_monitor.api_get(token, next_path, next_params)
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise GateError(f"GET {next_path} returned non-list data")
        items.extend(item for item in data if isinstance(item, dict))

        next_url = payload.get("links", {}).get("next")
        if not next_url:
            break
        parsed = urllib.parse.urlsplit(str(next_url))
        if parsed.scheme != "https" or parsed.netloc != "api.appstoreconnect.apple.com":
            raise GateError("ASC pagination link left api.appstoreconnect.apple.com")
        next_path = parsed.path
        parsed_query = urllib.parse.parse_qs(parsed.query)
        next_params = {
            key: values if len(values) > 1 else values[0]
            for key, values in parsed_query.items()
        }
    return items


def normalized_email(value: Any) -> str:
    return str(value or "").strip().casefold()


def exact_one(items: list[dict[str, Any]], description: str) -> dict[str, Any]:
    if len(items) != 1:
        raise GateError(f"expected exactly one {description}, found {len(items)}")
    return items[0]


def parse_asc_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise GateError(f"invalid ASC timestamp {text!r}") from error


def evaluate_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return named checks; callers fail if any check is false."""

    build = snapshot["build"]
    group = snapshot["group"]
    tester = snapshot["tester"]
    user = snapshot["asc_user"]
    roles = {str(role) for role in user.get("roles", [])}
    expiration = parse_asc_time(build.get("expiration_date"))
    now = datetime.now(timezone.utc)
    return [
        {"name": "build_processed", "ok": build.get("processing_state") == "VALID"},
        {
            "name": "build_not_expired",
            "ok": build.get("expired") is False and expiration is not None and expiration > now,
        },
        {"name": "ios_platform", "ok": build.get("platform") == "IOS"},
        {
            "name": "internal_audience_supported",
            "ok": build.get("audience") in {"APP_STORE_ELIGIBLE", "INTERNAL_ONLY"},
        },
        {"name": "minimum_os_declared", "ok": bool(build.get("minimum_os"))},
        {"name": "real_app_icon", "ok": build.get("has_icon") is True},
        {"name": "export_compliance_resolved", "ok": build.get("encryption_resolved") is True},
        {"name": "internal_testing_enabled", "ok": build.get("internal_build_state") == "IN_BETA_TESTING"},
        {"name": "internal_group", "ok": group.get("is_internal") is True},
        {"name": "group_grants_exact_build", "ok": group.get("build_access") is True},
        {"name": "tester_in_group", "ok": tester.get("in_group") is True},
        {"name": "tester_accepted", "ok": tester.get("state") in ACCEPTED_TESTER_STATES},
        {"name": "tester_is_asc_user", "ok": user.get("id") is not None},
        {"name": "eligible_internal_role", "ok": bool(roles & ELIGIBLE_INTERNAL_ROLES)},
        {"name": "user_can_access_app", "ok": user.get("can_access_app") is True},
    ]


def read_server_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    token = asc_monitor.mint_token(*asc_monitor.load_credentials())
    app = exact_one(
        asc_monitor.matching_apps(token, bundle_id=args.bundle, name_filter=None),
        f"ASC app for {args.bundle}",
    )
    app_id = str(app["id"])
    app_attrs = app.get("attributes", {})

    builds = api_get_all(
        token,
        "/v1/builds",
        {
            "filter[app]": app_id,
            "filter[version]": args.build,
            "limit": 200,
        },
    )
    build_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for candidate in builds:
        prerelease = asc_monitor.api_get(
            token,
            f"/v1/builds/{candidate['id']}/preReleaseVersion",
        ).get("data")
        if not isinstance(prerelease, dict):
            continue
        train = str(prerelease.get("attributes", {}).get("version", ""))
        if args.train and train != args.train:
            continue
        build_candidates.append((candidate, prerelease))
    if len(build_candidates) != 1:
        trains = [item[1].get("attributes", {}).get("version") for item in build_candidates]
        raise GateError(
            f"expected one build={args.build!r} train={args.train!r}, "
            f"found {len(build_candidates)} trains={trains}"
        )
    build, prerelease = build_candidates[0]
    build_id = str(build["id"])
    build_attrs = build.get("attributes", {})

    detail = asc_monitor.api_get(
        token,
        f"/v1/builds/{build_id}/buildBetaDetail",
    ).get("data")
    if not isinstance(detail, dict):
        raise GateError(f"build {build_id} has no buildBetaDetail")
    detail_attrs = detail.get("attributes", {})

    uses_encryption = build_attrs.get("usesNonExemptEncryption")
    encryption_resolved = uses_encryption is False
    encryption_declaration_id: str | None = None
    if uses_encryption is True:
        declaration = asc_monitor.api_get(
            token,
            f"/v1/builds/{build_id}/appEncryptionDeclaration",
            allow_not_found=True,
        ).get("data")
        if isinstance(declaration, dict):
            encryption_declaration_id = str(declaration.get("id"))
            encryption_resolved = True

    group_resources = api_get_all(
        token,
        "/v1/betaGroups",
        {"filter[app]": app_id, "limit": 200},
    )
    groups: list[dict[str, Any]] = []
    for resource in group_resources:
        attrs = resource.get("attributes", {})
        group_id = str(resource["id"])
        group_builds = api_get_all(
            token,
            f"/v1/betaGroups/{group_id}/builds",
            {"limit": 200},
        )
        group_testers = api_get_all(
            token,
            f"/v1/betaGroups/{group_id}/betaTesters",
            {"limit": 200},
        )
        build_ids = {str(item.get("id")) for item in group_builds}
        matching_testers = [
            item
            for item in group_testers
            if normalized_email(item.get("attributes", {}).get("email"))
            == normalized_email(args.tester)
        ]
        groups.append(
            {
                "resource": resource,
                "id": group_id,
                "name": attrs.get("name"),
                "is_internal": attrs.get("isInternalGroup"),
                "has_all_builds": attrs.get("hasAccessToAllBuilds"),
                "build_ids": sorted(build_ids),
                "build_access": attrs.get("hasAccessToAllBuilds") is True or build_id in build_ids,
                "matching_testers": matching_testers,
            }
        )

    if args.group:
        selected_groups = [item for item in groups if item["name"] == args.group]
    else:
        selected_groups = [
            item
            for item in groups
            if item["is_internal"] is True
            and item["build_access"] is True
            and len(item["matching_testers"]) == 1
        ]
    group = exact_one(selected_groups, f"eligible internal group {args.group or ''}".strip())
    tester_resource = exact_one(group["matching_testers"], f"tester {args.tester} in group {group['name']}")
    tester_attrs = tester_resource.get("attributes", {})

    users = api_get_all(token, "/v1/users", {"limit": 200})
    matching_users = [
        item
        for item in users
        if normalized_email(item.get("attributes", {}).get("username"))
        == normalized_email(args.tester)
    ]
    user = exact_one(matching_users, f"App Store Connect user {args.tester}")
    user_attrs = user.get("attributes", {})
    all_apps_visible = user_attrs.get("allAppsVisible") is True
    visible_app_ids: list[str] = []
    if not all_apps_visible:
        visible_apps = api_get_all(
            token,
            f"/v1/users/{user['id']}/visibleApps",
            {"limit": 200},
        )
        visible_app_ids = sorted(str(item.get("id")) for item in visible_apps)

    snapshot: dict[str, Any] = {
        "gate": "autoapp-toolkit/asc_testflight_readiness.py",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "app-store-connect-server-only",
        "app": {
            "id": app_id,
            "name": app_attrs.get("name"),
            "bundle_id": app_attrs.get("bundleId"),
        },
        "build": {
            "id": build_id,
            "build": build_attrs.get("version"),
            "train": prerelease.get("attributes", {}).get("version"),
            "platform": prerelease.get("attributes", {}).get("platform"),
            "processing_state": build_attrs.get("processingState"),
            "expired": build_attrs.get("expired"),
            "expiration_date": build_attrs.get("expirationDate"),
            "minimum_os": build_attrs.get("minOsVersion"),
            "audience": build_attrs.get("buildAudienceType"),
            "has_icon": bool(
                isinstance(build_attrs.get("iconAssetToken"), dict)
                and build_attrs.get("iconAssetToken", {}).get("templateUrl")
            ),
            "uses_non_exempt_encryption": uses_encryption,
            "encryption_declaration_id": encryption_declaration_id,
            "encryption_resolved": encryption_resolved,
            "internal_build_state": detail_attrs.get("internalBuildState"),
            "external_build_state": detail_attrs.get("externalBuildState"),
        },
        "group": {
            "id": group["id"],
            "name": group["name"],
            "is_internal": group["is_internal"],
            "has_all_builds": group["has_all_builds"],
            "build_access": group["build_access"],
            "build_ids": group["build_ids"],
        },
        "tester": {
            "id": tester_resource.get("id"),
            "email": tester_attrs.get("email"),
            "state": tester_attrs.get("state"),
            "invite_type": tester_attrs.get("inviteType"),
            "in_group": True,
        },
        "asc_user": {
            "id": user.get("id"),
            "username": user_attrs.get("username"),
            "roles": user_attrs.get("roles", []),
            "all_apps_visible": all_apps_visible,
            "visible_app_ids": visible_app_ids,
            "can_access_app": all_apps_visible or app_id in visible_app_ids,
        },
        "manual_gates_not_exposed_by_asc_api": [
            "which Apple Account redeemed the invitation on the target device",
            "target-device compatibility and current TestFlight session",
            "exact build installed on the target device",
            "Business agreement, tax, and banking status for paid apps or IAP",
        ],
    }
    snapshot["checks"] = evaluate_snapshot(snapshot)
    snapshot["server_gate_ok"] = all(item["ok"] for item in snapshot["checks"])
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only ASC gate for one internal TestFlight tester/build/group chain."
    )
    parser.add_argument("--bundle", required=True, help="exact app bundle identifier")
    parser.add_argument("--build", required=True, help="exact CFBundleVersion")
    parser.add_argument("--train", help="exact marketing-version train, recommended")
    parser.add_argument("--tester", required=True, help="exact internal tester/ASC username email")
    parser.add_argument("--group", help="exact internal beta-group name")
    parser.add_argument("--receipt", type=Path, help="atomically write JSON evidence")
    parser.add_argument("--json", action="store_true", help="print full JSON evidence")
    args = parser.parse_args(argv)

    try:
        snapshot = read_server_snapshot(args)
        if args.receipt:
            asc_monitor.write_snapshot(args.receipt, snapshot)
        failed = [item["name"] for item in snapshot["checks"] if not item["ok"]]
        if failed:
            raise GateError("failed checks: " + ", ".join(failed))
    except (asc_monitor.ASCError, GateError, OSError) as error:
        print(f"ASC_TESTFLIGHT_SERVER_GATE_FAILED: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "ASC_TESTFLIGHT_SERVER_GATE_OK "
            f"bundle={snapshot['app']['bundle_id']} "
            f"train={snapshot['build']['train']} build={snapshot['build']['build']} "
            f"group={snapshot['group']['name']!r} tester={snapshot['tester']['email']} "
            f"tester_state={snapshot['tester']['state']} "
            "scope=server-only device_install=unproven"
        )
        if args.receipt:
            print(f"receipt={args.receipt.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
