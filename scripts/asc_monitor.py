#!/usr/bin/env python3
"""Mac-native, read-only App Store Connect release-state probe.

The probe intentionally reports the four independent pieces of release state
that must never be collapsed into a single "ship done" claim:

* App Store versions and their selected builds;
* recent TestFlight builds and version trains;
* in-app purchases and localization review states;
* review submissions and their items.

Credentials are read from environment variables or the private per-user
``~/.appstoreconnect`` directory. They are never printed or written to the
repository.
"""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ASC_BASE = "https://api.appstoreconnect.apple.com"
KEY_DIR = Path.home() / ".appstoreconnect" / "private_keys"
CONFIG_PATH = Path.home() / ".appstoreconnect" / "config.json"
STATE_FIELDS = ("appStoreState", "appVersionState", "state")


class ASCError(RuntimeError):
    """A sanitized App Store Connect API failure."""


def load_credentials() -> tuple[str, str, Path]:
    config: dict[str, str] = {}
    if CONFIG_PATH.is_file():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                config = {str(key): str(value) for key, value in raw.items()}
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ASCError(f"cannot read {CONFIG_PATH}: {error}") from error

    key_id = os.environ.get("ASC_KEY_ID") or config.get("key_id", "")
    issuer_id = os.environ.get("ASC_ISSUER_ID") or config.get("issuer_id", "")

    if not key_id:
        candidates = sorted(glob.glob(str(KEY_DIR / "AuthKey_*.p8")))
        if len(candidates) == 1:
            key_id = Path(candidates[0]).stem.removeprefix("AuthKey_")

    missing: list[str] = []
    if not key_id:
        missing.append("ASC key id")
    if not issuer_id or issuer_id.lower().startswith("xxxx"):
        missing.append("ASC issuer id")

    key_path = KEY_DIR / f"AuthKey_{key_id}.p8"
    if key_id and not key_path.is_file():
        missing.append(f"private key {key_path}")
    if missing:
        raise ASCError("missing credentials: " + ", ".join(missing))

    try:
        private_key = key_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ASCError(f"cannot read private key {key_path}: {error}") from error
    if "BEGIN PRIVATE KEY" not in private_key and "BEGIN EC PRIVATE KEY" not in private_key:
        raise ASCError(f"private key {key_path} is not a PEM private key")
    return issuer_id, key_id, key_path


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _der_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ASCError("cannot create ASC JWT: truncated DER length")
    first = data[offset]
    offset += 1
    if first < 0x80:
        return first, offset
    octets = first & 0x7F
    if octets == 0 or octets > 4 or offset + octets > len(data):
        raise ASCError("cannot create ASC JWT: invalid DER length")
    return int.from_bytes(data[offset : offset + octets], "big"), offset + octets


def es256_der_to_raw(signature: bytes) -> bytes:
    if not signature or signature[0] != 0x30:
        raise ASCError("cannot create ASC JWT: invalid DER sequence")
    sequence_length, offset = _der_length(signature, 1)
    if offset + sequence_length != len(signature):
        raise ASCError("cannot create ASC JWT: invalid DER sequence length")
    values: list[bytes] = []
    for _ in range(2):
        if offset >= len(signature) or signature[offset] != 0x02:
            raise ASCError("cannot create ASC JWT: invalid DER integer")
        length, offset = _der_length(signature, offset + 1)
        if length == 0 or offset + length > len(signature):
            raise ASCError("cannot create ASC JWT: truncated DER integer")
        value = signature[offset : offset + length]
        offset += length
        value = value.lstrip(b"\x00") or b"\x00"
        if len(value) > 32:
            raise ASCError("cannot create ASC JWT: oversized ES256 integer")
        values.append(value.rjust(32, b"\x00"))
    if offset != len(signature):
        raise ASCError("cannot create ASC JWT: trailing DER data")
    return b"".join(values)


def mint_token(issuer_id: str, key_id: str, private_key_path: Path) -> str:
    now = int(time.time())
    header = b64url(json.dumps(
        {"alg": "ES256", "kid": key_id, "typ": "JWT"},
        separators=(",", ":"),
    ).encode("utf-8"))
    payload = b64url(json.dumps(
        {
            "iss": issuer_id,
            "iat": now - 5,
            "exp": now + 1_000,
            "aud": "appstoreconnect-v1",
        },
        separators=(",", ":"),
    ).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    try:
        process = subprocess.run(
            ["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(private_key_path)],
            input=signing_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ASCError(f"cannot create ASC JWT with /usr/bin/openssl: {error}") from error
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", "replace").strip()[:500]
        raise ASCError(f"cannot create ASC JWT with /usr/bin/openssl: {detail}")
    raw_signature = es256_der_to_raw(process.stdout)
    return f"{header}.{payload}.{b64url(raw_signature)}"


def api_url(path: str, params: dict[str, Any] | None = None) -> str:
    if not path.startswith("/"):
        raise ValueError(f"ASC path must start with '/': {path}")
    query = urllib.parse.urlencode(params or {}, doseq=True)
    return f"{ASC_BASE}{path}" + (f"?{query}" if query else "")


def api_get(
    token: str,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    allow_not_found: bool = False,
) -> dict[str, Any]:
    url = api_url(path, params)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "autoapp-toolkit-asc-monitor/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if allow_not_found and error.code == 404:
            return {"data": None}
        body = error.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
            details = "; ".join(
                f"{item.get('code', '?')}: {item.get('detail', item.get('title', ''))}"
                for item in parsed.get("errors", [])
            )
        except json.JSONDecodeError:
            details = " ".join(body.split())[:600]
        hint = " (verify issuer/key permissions)" if error.code in {401, 403} else ""
        raise ASCError(f"GET {path} returned {error.code}{hint}: {details}") from error
    except (OSError, TimeoutError, json.JSONDecodeError) as error:
        raise ASCError(f"GET {path} failed: {type(error).__name__}: {error}") from error
    if not isinstance(payload, dict):
        raise ASCError(f"GET {path} returned a non-object JSON payload")
    return payload


def state_of(attributes: dict[str, Any]) -> str:
    for field in STATE_FIELDS:
        value = attributes.get(field)
        if value:
            return str(value)
    return "UNKNOWN"


def relationship_id(resource: dict[str, Any], name: str) -> str | None:
    data = resource.get("relationships", {}).get(name, {}).get("data")
    if isinstance(data, dict):
        value = data.get("id")
        return str(value) if value else None
    return None


def relationship_ids(resource: dict[str, Any], name: str) -> list[str]:
    data = resource.get("relationships", {}).get(name, {}).get("data")
    if not isinstance(data, list):
        return []
    return [str(item["id"]) for item in data if isinstance(item, dict) and item.get("id")]


def included_index(payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in payload.get("included", []):
        if isinstance(item, dict) and item.get("type") and item.get("id"):
            index[(str(item["type"]), str(item["id"]))] = item
    return index


def read_iap_versions(token: str, iap_id: str) -> list[dict[str, Any]]:
    payload = api_get(
        token,
        f"/v2/inAppPurchases/{iap_id}/versions",
        {"limit": 200},
    )
    versions: list[dict[str, Any]] = []
    for version in payload.get("data", []):
        version_id = str(version["id"])
        localization_payload = api_get(
            token,
            f"/v1/inAppPurchaseVersions/{version_id}/localizations",
            {"limit": 50},
        )
        localizations = [
            {
                "id": item.get("id"),
                "locale": item.get("attributes", {}).get("locale"),
                "name": item.get("attributes", {}).get("name"),
                "description": item.get("attributes", {}).get("description"),
            }
            for item in localization_payload.get("data", [])
        ]
        versions.append(
            {
                "id": version_id,
                "version": version.get("attributes", {}).get("version"),
                "state": version.get("attributes", {}).get("state"),
                "localizations": sorted(
                    localizations,
                    key=lambda item: str(item.get("locale", "")),
                ),
            }
        )
    return sorted(versions, key=lambda item: int(item.get("version") or 0))


def read_iap_price(token: str, iap_id: str, territory: str = "USA") -> dict[str, Any]:
    payload = api_get(
        token,
        f"/v1/inAppPurchasePriceSchedules/{iap_id}/manualPrices",
        {
            "filter[territory]": territory,
            "include": "inAppPurchasePricePoint,territory",
            "fields[inAppPurchasePricePoints]": "customerPrice,proceeds",
            "limit": 50,
        },
    )
    index = included_index(payload)
    active: list[dict[str, Any]] = []
    for price in payload.get("data", []):
        if relationship_id(price, "territory") != territory:
            continue
        attributes = price.get("attributes", {})
        if attributes.get("endDate") is not None:
            continue
        point_id = relationship_id(price, "inAppPurchasePricePoint")
        point = index.get(("inAppPurchasePricePoints", point_id or ""), {})
        territory_resource = index.get(("territories", territory), {})
        active.append(
            {
                "schedule_price_id": price.get("id"),
                "price_point_id": point_id,
                "territory": territory,
                "currency": territory_resource.get("attributes", {}).get("currency"),
                "customer_price": point.get("attributes", {}).get("customerPrice"),
                "proceeds": point.get("attributes", {}).get("proceeds"),
                "start_date": attributes.get("startDate"),
                "end_date": attributes.get("endDate"),
                "manual": attributes.get("manual"),
            }
        )
    if len(active) != 1:
        raise ASCError(
            f"IAP {iap_id} expected one active {territory} manual price, found {len(active)}"
        )
    return active[0]


def read_iap_availability(token: str, iap_id: str) -> dict[str, Any]:
    payload = api_get(
        token,
        f"/v2/inAppPurchases/{iap_id}/inAppPurchaseAvailability",
        {"include": "availableTerritories", "limit[availableTerritories]": 50},
    )
    resource = payload.get("data")
    if not isinstance(resource, dict):
        raise ASCError(f"IAP {iap_id} availability response has no resource")
    return {
        "available_in_new_territories": resource.get("attributes", {}).get(
            "availableInNewTerritories"
        ),
        "territory_count": resource.get("relationships", {})
        .get("availableTerritories", {})
        .get("meta", {})
        .get("paging", {})
        .get("total"),
        "first_page_territories": relationship_ids(resource, "availableTerritories"),
    }


def read_iap_screenshot(token: str, iap_id: str) -> dict[str, Any] | None:
    payload = api_get(
        token,
        f"/v2/inAppPurchases/{iap_id}/appStoreReviewScreenshot",
        allow_not_found=True,
    )
    resource = payload.get("data")
    if not isinstance(resource, dict):
        return None
    attributes = resource.get("attributes", {})
    image = attributes.get("imageAsset", {})
    return {
        "id": resource.get("id"),
        "file_name": attributes.get("fileName"),
        "file_size": attributes.get("fileSize"),
        "source_file_checksum": attributes.get("sourceFileChecksum"),
        "delivery_state": attributes.get("assetDeliveryState", {}).get("state"),
        "width": image.get("width"),
        "height": image.get("height"),
    }


def matching_apps(
    token: str, *, bundle_id: str | None, name_filter: str | None
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": 200, "fields[apps]": "name,bundleId"}
    if bundle_id:
        params["filter[bundleId]"] = bundle_id
    apps = api_get(token, "/v1/apps", params).get("data", [])
    if name_filter:
        needle = name_filter.casefold()
        apps = [
            app
            for app in apps
            if needle in str(app.get("attributes", {}).get("name", "")).casefold()
            or needle in str(app.get("attributes", {}).get("bundleId", "")).casefold()
        ]
    if bundle_id or name_filter:
        if len(apps) != 1:
            names = [item.get("attributes", {}).get("name") for item in apps]
            raise ASCError(f"expected exactly one matching app, found {len(apps)}: {names}")
    if not apps:
        names = [item.get("attributes", {}).get("name") for item in apps]
        raise ASCError(f"App Store Connect returned no matching apps: {names}")
    return sorted(apps, key=lambda item: str(item.get("attributes", {}).get("name", "")))


def read_version_build(token: str, version_id: str) -> dict[str, Any] | None:
    payload = api_get(
        token,
        f"/v1/appStoreVersions/{version_id}/build",
        {"fields[builds]": "version,uploadedDate,processingState,expired"},
        allow_not_found=True,
    )
    build = payload.get("data")
    if not isinstance(build, dict):
        return None
    attrs = build.get("attributes", {})
    return {
        "id": build.get("id"),
        "build": attrs.get("version"),
        "processing_state": attrs.get("processingState"),
        "uploaded_date": attrs.get("uploadedDate"),
        "expired": attrs.get("expired"),
    }


def read_release_state(token: str, app: dict[str, Any]) -> dict[str, Any]:
    app_id = str(app["id"])
    attrs = app.get("attributes", {})

    version_payload = api_get(
        token,
        f"/v1/apps/{app_id}/appStoreVersions",
        {
            "limit": 200,
            "filter[platform]": "IOS",
            "fields[appStoreVersions]": (
                "platform,versionString,appStoreState,appVersionState,createdDate,releaseType"
            ),
        },
    )
    versions: list[dict[str, Any]] = []
    for version in version_payload.get("data", []):
        version_attrs = version.get("attributes", {})
        versions.append(
            {
                "id": version.get("id"),
                "version": version_attrs.get("versionString"),
                "state": state_of(version_attrs),
                "platform": version_attrs.get("platform"),
                "created_date": version_attrs.get("createdDate"),
                "release_type": version_attrs.get("releaseType"),
                "selected_build": read_version_build(token, str(version["id"])),
            }
        )

    build_payload = api_get(
        token,
        "/v1/builds",
        {
            "filter[app]": app_id,
            "sort": "-uploadedDate",
            "limit": 50,
            "include": "preReleaseVersion",
            "fields[builds]": (
                "version,uploadedDate,processingState,expired,minOsVersion,usesNonExemptEncryption,preReleaseVersion"
            ),
            "fields[preReleaseVersions]": "version,platform",
        },
    )
    build_included = included_index(build_payload)
    builds: list[dict[str, Any]] = []
    for build in build_payload.get("data", []):
        build_attrs = build.get("attributes", {})
        train_id = relationship_id(build, "preReleaseVersion")
        train = build_included.get(("preReleaseVersions", train_id or ""), {})
        train_attrs = train.get("attributes", {})
        builds.append(
            {
                "id": build.get("id"),
                "build": build_attrs.get("version"),
                "train": train_attrs.get("version"),
                "platform": train_attrs.get("platform"),
                "processing_state": build_attrs.get("processingState"),
                "uploaded_date": build_attrs.get("uploadedDate"),
                "expired": build_attrs.get("expired"),
                "min_os_version": build_attrs.get("minOsVersion"),
                "uses_non_exempt_encryption": build_attrs.get("usesNonExemptEncryption"),
            }
        )

    iap_payload = api_get(
        token,
        f"/v1/apps/{app_id}/inAppPurchasesV2",
        {"limit": 200},
    )
    iaps: list[dict[str, Any]] = []
    for iap in iap_payload.get("data", []):
        iap_attrs = iap.get("attributes", {})
        iap_id = str(iap["id"])
        iap_versions = read_iap_versions(token, iap_id)
        latest_localizations = iap_versions[-1]["localizations"] if iap_versions else []
        iaps.append(
            {
                "id": iap_id,
                "product_id": iap_attrs.get("productId"),
                "name": iap_attrs.get("name"),
                "type": iap_attrs.get("inAppPurchaseType"),
                "state": iap_attrs.get("state"),
                "review_note": iap_attrs.get("reviewNote"),
                "versions": iap_versions,
                "localizations": latest_localizations,
                "base_price": read_iap_price(token, iap_id),
                "availability": read_iap_availability(token, iap_id),
                "review_screenshot": read_iap_screenshot(token, iap_id),
            }
        )

    submission_payload = api_get(
        token,
        f"/v1/apps/{app_id}/reviewSubmissions",
        {
            "limit": 200,
            "include": "items,appStoreVersionForReview",
            "limit[items]": 50,
            "fields[reviewSubmissions]": (
                "platform,submittedDate,state,items,appStoreVersionForReview"
            ),
            "fields[reviewSubmissionItems]": "state,appStoreVersion",
            "fields[appStoreVersions]": "platform,versionString,appStoreState,appVersionState",
        },
    )
    submission_included = included_index(submission_payload)
    version_by_id = {str(item["id"]): item for item in version_payload.get("data", [])}
    version_by_id.update(
        {
            resource_id: resource
            for (resource_type, resource_id), resource in submission_included.items()
            if resource_type == "appStoreVersions"
        }
    )

    submissions: list[dict[str, Any]] = []
    for submission in submission_payload.get("data", []):
        submission_attrs = submission.get("attributes", {})
        version_id = relationship_id(submission, "appStoreVersionForReview")
        version_resource = version_by_id.get(version_id or "", {})
        submission_items: list[dict[str, Any]] = []
        for item_id in relationship_ids(submission, "items"):
            item = submission_included.get(("reviewSubmissionItems", item_id), {})
            item_attrs = item.get("attributes", {})
            item_version_id = relationship_id(item, "appStoreVersion")
            item_version = version_by_id.get(item_version_id or "", {})
            submission_items.append(
                {
                    "id": item_id,
                    "state": item_attrs.get("state"),
                    "app_store_version_id": item_version_id,
                    "version": item_version.get("attributes", {}).get("versionString"),
                }
            )
        submissions.append(
            {
                "id": submission.get("id"),
                "state": submission_attrs.get("state"),
                "platform": submission_attrs.get("platform"),
                "submitted_date": submission_attrs.get("submittedDate"),
                "app_store_version_id": version_id,
                "version": version_resource.get("attributes", {}).get("versionString"),
                "items": submission_items,
            }
        )

    return {
        "polled_at": datetime.now(timezone.utc).isoformat(),
        "app": {
            "id": app_id,
            "name": attrs.get("name"),
            "bundle_id": attrs.get("bundleId"),
        },
        "versions": versions,
        "builds": builds,
        "in_app_purchases": iaps,
        "review_submissions": submissions,
    }


def print_text(report: dict[str, Any]) -> None:
    app = report["app"]
    print(f"ASC LIVE PROBE — {report['polled_at']}")
    print(f"app: {app['name']} ({app['bundle_id']}) id={app['id']}")
    print("\nApp Store versions")
    for version in report["versions"]:
        selected = version["selected_build"]
        build_text = "none"
        if selected:
            build_text = (
                f"{selected['build']} id={selected['id']} "
                f"processing={selected['processing_state']}"
            )
        print(
            f"- {version['version']} state={version['state']} id={version['id']} "
            f"selected_build={build_text}"
        )

    print("\nRecent TestFlight builds")
    if not report["builds"]:
        print("- none")
    for build in report["builds"]:
        print(
            f"- train={build['train']} build={build['build']} "
            f"processing={build['processing_state']} id={build['id']} "
            f"uploaded={build['uploaded_date']}"
        )

    print("\nIn-app purchases")
    if not report["in_app_purchases"]:
        print("- none")
    for iap in report["in_app_purchases"]:
        versions = ", ".join(
            f"v{item['version']}={item['state']}({len(item['localizations'])} locales)"
            for item in iap["versions"]
        ) or "none"
        price = iap["base_price"]
        availability = iap["availability"]
        screenshot = iap["review_screenshot"]
        screenshot_text = "none"
        if screenshot:
            screenshot_text = (
                f"{screenshot['delivery_state']} {screenshot['width']}x{screenshot['height']} "
                f"md5={screenshot['source_file_checksum']}"
            )
        print(
            f"- {iap['product_id']} state={iap['state']} id={iap['id']} "
            f"base={price['currency']} {price['customer_price']} "
            f"territories={availability['territory_count']} "
            f"new_territories={availability['available_in_new_territories']} "
            f"versions=[{versions}] screenshot=[{screenshot_text}]"
        )

    print("\nReview submissions")
    if not report["review_submissions"]:
        print("- none")
    for submission in report["review_submissions"]:
        item_text = ", ".join(
            f"{item['version'] or item['app_store_version_id']}={item['state']}"
            for item in submission["items"]
        ) or "none"
        print(
            f"- version={submission['version']} state={submission['state']} "
            f"id={submission['id']} submitted={submission['submitted_date']} "
            f"items=[{item_text}]"
        )


def snapshot_app(report: dict[str, Any]) -> dict[str, Any]:
    app = report["app"]
    return {
        "name": app["name"],
        "bundle": app["bundle_id"],
        "app_id": app["id"],
        "versions": [
            {
                "version_id": version["id"],
                "versionString": version["version"],
                "appStoreState": version["state"],
                "platform": version["platform"],
                "releaseType": version["release_type"],
                "createdDate": version["created_date"],
                "selected_build": version["selected_build"],
            }
            for version in report["versions"]
        ],
        "builds": report["builds"],
        "iaps": [
            {
                "iap_id": iap["id"],
                "name": iap["name"],
                "productId": iap["product_id"],
                "type": iap["type"],
                "state": iap["state"],
                "review_note": iap["review_note"],
                "versions": iap["versions"],
                "localizations": iap["localizations"],
                "base_price": iap["base_price"],
                "availability": iap["availability"],
                "review_screenshot": iap["review_screenshot"],
            }
            for iap in report["in_app_purchases"]
        ],
        "review_submissions": [
            {
                "submission_id": submission["id"],
                "state": submission["state"],
                "submittedDate": submission["submitted_date"],
                "platform": submission["platform"],
                "version": submission["version"],
                "app_store_version_id": submission["app_store_version_id"],
                "items": submission["items"],
            }
            for submission in report["review_submissions"]
        ],
    }


def portfolio_snapshot(reports: list[dict[str, Any]]) -> dict[str, Any]:
    polled_at = max((report["polled_at"] for report in reports), default=None)
    return {
        "monitor": "autoapp-toolkit/asc_monitor.py",
        "polled_at": polled_at,
        "apps": [snapshot_app(report) for report in reports],
    }


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except (OSError, UnicodeError) as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ASCError(f"cannot write snapshot {path}: {error}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--bundle", help="exact app bundle identifier")
    selector.add_argument("--app", help="case-insensitive app name or bundle substring")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="atomically write a normalized portfolio snapshot to this path",
    )
    args = parser.parse_args(argv)

    try:
        token = mint_token(*load_credentials())
        apps = matching_apps(token, bundle_id=args.bundle, name_filter=args.app)
        reports = [read_release_state(token, app) for app in apps]
        snapshot = portfolio_snapshot(reports)
        if args.snapshot:
            write_snapshot(args.snapshot, snapshot)
    except ASCError as error:
        print(f"ASC LIVE PROBE FAILED: {error}", file=sys.stderr)
        return 1

    if args.json:
        payload: dict[str, Any] = reports[0] if len(reports) == 1 else snapshot
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for index, report in enumerate(reports):
            if index:
                print("\n" + "=" * 88 + "\n")
            print_text(report)
        if args.snapshot:
            print(f"\nsnapshot={args.snapshot.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
