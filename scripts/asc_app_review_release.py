#!/usr/bin/env python3
"""Release one already processed, staged app and its existing IAP metadata.

Default: authenticated read-only preflight. This controller does not upload a
binary, change metadata/screenshots/prices, or relax app-specific device gates.
Writes require --submit and an exact confirmation; every request is journaled
before transmission. An uncertain request is recovered by GET, never retried.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import re
import struct
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asc_app_store_stage as stage
import asc_monitor as asc


class ReleaseError(RuntimeError):
    """Required release evidence or an unambiguous server outcome is missing."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseError(f"cannot read JSON evidence: {path.name}") from error
    require(isinstance(value, dict), f"evidence must be an object: {path.name}")
    return value


def save(path: Path, value: dict[str, Any]) -> None:
    require(not path.is_symlink(), "receipt cannot be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".pending")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@dataclass(frozen=True)
class Spec:
    repo: Path
    app_id: str
    iap_id: str
    product_id: str
    version: str
    build: str
    release_receipt: Path
    metadata_receipt: Path
    iap_metadata_receipt: Path
    iap_image_receipt: Path
    paywall_image: Path
    store_image_receipt: Path
    stage_receipt: Path
    independent_preflight: Path
    receipt: Path


def all_checks_pass(payload: dict[str, Any], label: str) -> None:
    checks = payload.get("checks")
    require(isinstance(checks, list) and bool(checks), f"{label} has no checks")
    require(all(isinstance(c, dict) and any(k in c for k in ["pass", "ok"])
                and all(c[k] is True for k in ["pass", "ok"] if k in c)
                for c in checks), f"{label} contains a failed or unproved check")


class Evidence:
    def __init__(self, spec: Spec):
        self.spec = spec
        require(bool(re.fullmatch(r"\d+\.\d+\.\d+", spec.version))
                and any(int(x) > 0 for x in spec.version.split(".")), "version must be positive x.y.z")
        require(bool(re.fullmatch(r"[1-9]\d*", spec.build)), "build must be a positive integer")
        require(all(bool(re.fullmatch(r"[A-Za-z0-9.-]+", v)) for v in
                    [spec.app_id, spec.iap_id, spec.product_id]), "invalid app, IAP, or product identifier")
        paths = [spec.release_receipt, spec.metadata_receipt, spec.iap_metadata_receipt,
                 spec.iap_image_receipt, spec.stage_receipt, spec.independent_preflight, spec.paywall_image]
        require(spec.receipt.resolve() not in [p.resolve() for p in paths], "receipt aliases input evidence")
        self.files = {str(p.resolve()): digest(p) for p in paths}
        self.release, self.metadata, self.iap, self.image, self.staged, self.proof = [
            read_json(p) for p in paths[:-1]]
        self.store_images = read_json(spec.store_image_receipt)
        self.files[str(spec.store_image_receipt.resolve())] = digest(spec.store_image_receipt)
        self.bundle_id = self.release.get("app", {}).get("bundle_id")
        self.name = self.release.get("app", {}).get("name")
        require(isinstance(self.bundle_id, str) and self.bundle_id and isinstance(self.name, str)
                and bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", self.name)), "missing app identity")
        self.sha = self.release.get("source", {}).get("git_sha")
        require(isinstance(self.sha, str) and bool(re.fullmatch(r"[0-9a-f]{40}", self.sha)), "invalid source SHA")
        self.build_id = self.release.get("testflight", {}).get("id")
        self.version_id = self.metadata.get("version_id")
        self.iap_version_id = self.iap.get("iap_metadata_version_id")
        require(all(isinstance(v, str) and bool(re.fullmatch(r"[A-Za-z0-9-]+", v))
                    for v in [self.build_id, self.version_id, self.iap_version_id]), "missing exact ASC resource IDs")
        expected = {"bundle_id": self.bundle_id, "version": spec.version, "build": spec.build}
        for section in ["app", "artifact"]:
            actual = self.release.get(section, {})
            require(all(actual.get(k) == v for k, v in expected.items()), f"processed {section} identity mismatch")
        artifact = self.release.get("artifact", {})
        ipa_path = Path(artifact.get("ipa_path", ""))
        if not ipa_path.is_absolute():
            ipa_path = spec.release_receipt.parent / ipa_path
        require(ipa_path.is_file() and digest(ipa_path) == artifact.get("ipa_sha256")
                and artifact.get("codesign") == "passed"
                and self.release.get("claims", {}).get("signed_artifact_verified") is True,
                "processed signed IPA is missing or changed")
        self.files[str(ipa_path.resolve())] = digest(ipa_path)
        try:
            with zipfile.ZipFile(ipa_path) as archive:
                entries = [entry for entry in archive.infolist() if len(Path(entry.filename).parts) == 3
                           and Path(entry.filename).parts[0] == "Payload"
                           and Path(entry.filename).parts[1].endswith(".app")
                           and Path(entry.filename).parts[2] == "Info.plist"]
                require(len(entries) == 1 and entries[0].file_size <= 1024 * 1024, "IPA app identity is ambiguous")
                ipa_info = plistlib.loads(archive.read(entries[0]))
        except (zipfile.BadZipFile, plistlib.InvalidFileException) as error:
            raise ReleaseError("processed IPA identity is unreadable") from error
        require(ipa_info.get("CFBundleIdentifier") == self.bundle_id
                and ipa_info.get("CFBundleShortVersionString") == spec.version
                and ipa_info.get("CFBundleVersion") == spec.build, "processed IPA app/version/build mismatch")
        self.device_families = ipa_info.get("UIDeviceFamily")
        require(self.device_families in [[1], [1, 2]], "processed IPA device families are not proven iOS phone/tablet")
        require(self.release.get("stage") == "testflight-processed", "release receipt is not processed")
        require(self.release.get("claims", {}).get("testflight_processed") is True
                and self.release.get("claims", {}).get("source_remote_readback") is True,
                "processed release/source remote claims missing")
        source = self.release.get("source", {})
        require(source.get("clean") is True and source.get("branch") == "main"
                and source.get("remote_sha") == self.sha and source.get("tracking_sha") == self.sha
                and source.get("ahead") == 0 and source.get("behind") == 0, "processed source was not clean exact main")
        require(self.metadata.get("draft_metadata_applied") is True
                and self.metadata.get("source_git_sha") == self.sha
                and self.metadata.get("app_id") == spec.app_id
                and self.metadata.get("build_id") == self.build_id
                and self.metadata.get("version") == spec.version
                and str(self.metadata.get("build")) == spec.build, "metadata receipt identity mismatch")
        require(self.iap.get("iap_metadata_applied") is True and self.iap.get("iap_id") == spec.iap_id
                and self.iap.get("parent_product_id_unchanged") == spec.product_id
                and self.iap.get("price_writes_performed") is False, "IAP metadata receipt mismatch or price writes")
        require(self.iap.get("iap_metadata_version_readback", {}).get("id") == self.iap_version_id,
                "IAP metadata version readback mismatch")
        require(self.image.get("complete") is True and self.image.get("parent_product_id") == spec.product_id
                and self.image.get("price_writes_performed") is False
                and self.image.get("current_paywall_sha256") == digest(spec.paywall_image),
                "current paywall image evidence mismatch")
        image_readback = self.image.get("readback", {})
        require(image_readback.get("id") == self.image.get("new_asset_id")
                and image_readback.get("attributes", {}).get("sourceFileChecksum") == digest(spec.paywall_image, "md5")
                and image_readback.get("attributes", {}).get("assetDeliveryState", {}).get("state") == "COMPLETE",
                "image readback is not exact COMPLETE current pixels")
        visual_path = Path(self.image.get("capture_and_visual_receipt", ""))
        if not visual_path.is_absolute():
            visual_path = spec.iap_image_receipt.parent / visual_path
        visual = read_json(visual_path)
        self.files[str(visual_path.resolve())] = digest(visual_path)
        require(visual.get("visual_review_completed") is True and visual.get("sha256") == digest(spec.paywall_image),
                "current paywall visual review is not exact")
        require(self.staged.get("staged_ok") is True and self.staged.get("source_git_sha") == self.sha
                and self.staged.get("app", {}).get("id") == spec.app_id
                and self.staged.get("app", {}).get("bundle_id") == self.bundle_id
                and self.staged.get("version", {}).get("id") == self.version_id
                and self.staged.get("version", {}).get("version") == spec.version
                and self.staged.get("version", {}).get("selected_build", {}).get("id") == self.build_id,
                "staging receipt identity mismatch")
        all_checks_pass(self.staged, "stage")
        p = self.proof
        require(p.get("ready_for_submission") is True and p.get("independent_reviewer_no_mutations") is True
                and p.get("remaining_blockers") == [], "independent preflight is not ready")
        expected_proof = {"app_id": spec.app_id, "bundle_id": self.bundle_id, "version": spec.version,
                          "build_id": self.build_id, "source_git_sha": self.sha,
                          "app_store_version_id": self.version_id, "new_iap_metadata_version_id": self.iap_version_id}
        require(all(p.get(k) == v for k, v in expected_proof.items()) and str(p.get("build")) == spec.build,
                "independent preflight identity mismatch")
        all_checks_pass(p, "independent preflight")
        require(self.store_images.get("screenshots_complete") is True
                and self.store_images.get("source_git_sha") == self.sha
                and self.store_images.get("requires_ipad") is (2 in self.device_families),
                "store screenshot receipt is not source-bound COMPLETE with actual device families")
        plan_path = Path(self.store_images.get("selected_plan", ""))
        if not plan_path.is_absolute():
            plan_path = spec.store_image_receipt.parent / plan_path
        plan = read_json(plan_path)
        self.files[str(plan_path.resolve())] = digest(plan_path)
        require(plan.get("visual_review_completed") is True and plan.get("source_git_sha") == self.sha
                and plan.get("requires_ipad") is self.store_images["requires_ipad"], "store screenshot selection/visual proof mismatch")
        uploaded = self.store_images.get("uploaded")
        selected = plan.get("images")
        require(isinstance(uploaded, list) and bool(uploaded) and isinstance(selected, list) and bool(selected),
                "store screenshot exact asset mapping missing")
        selected_by_key = {(r.get("locale"), r.get("display_type"), r.get("sha256")): r for r in selected}
        require(len(selected_by_key) == len(selected) and len(uploaded) == len(selected), "duplicate or partial store screenshot mapping")
        used = set()
        for uploaded_image in uploaded:
            key = (uploaded_image.get("locale"), uploaded_image.get("display_type"), uploaded_image.get("source_sha256"))
            require(key in selected_by_key and key not in used, "uploaded image is not in current reviewed selection")
            used.add(key)
            selected_image = selected_by_key[key]
            path = Path(selected_image.get("path", ""))
            if not path.is_absolute():
                path = plan_path.parent / path
            require(path.is_file() and digest(path) == key[2]
                    and digest(path, "md5") == uploaded_image.get("source_file_checksum"), "store screenshot pixels changed")
            with path.open("rb") as stream:
                header = stream.read(24)
            require(len(header) == 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR", "store screenshot is not a PNG")
            dimensions = list(struct.unpack(">II", header[16:24]))
            require(dimensions == selected_image.get("dimensions") == uploaded_image.get("dimensions"),
                    "store screenshot dimensions mismatch")
            require(all(isinstance(uploaded_image.get(k), str) and uploaded_image[k] for k in ["id", "set_id"]),
                    "store screenshot asset identity missing")
            self.files[str(path.resolve())] = digest(path)
        require(len({r["id"] for r in uploaded}) == len(uploaded), "duplicate store screenshot asset ID")
        locales = {r.get("locale") for r in self.metadata.get("readbacks", [])}
        required_locales = {"en-US", "ja", "zh-Hans", "zh-Hant", "ko", "es-ES", "fr-FR", "de-DE"}
        require(locales == required_locales, "current app metadata must cover eight release locales")
        require(all(r.get("locale") in locales for r in uploaded), "store screenshot has an unsupported locale")
        require(any(r.get("locale") == "en-US" and r.get("display_type") in stage.IPHONE_SCREENSHOT_TYPES
                    for r in uploaded), "current primary English phone screenshot missing")
        if self.store_images["requires_ipad"]:
            require(any(r.get("locale") == "en-US" and r.get("display_type") in stage.IPAD_SCREENSHOT_TYPES
                        for r in uploaded), "current primary native iPad screenshot missing")
        if self.bundle_id in {"com.jiejuefuyou.promptvault", "com.jiejuefuyou.waternow"}:
            require(p.get("physical_release_gate_completed") is True
                    and p.get("exact_processed_build_installed_on_physical_device") is True,
                    "mandatory physical release gate / exact processed device installation missing")
        if self.bundle_id == "com.jiejuefuyou.promptvault":
            require(p.get("two_same_account_real_devices_including_ipad") is True,
                    "PromptVault requires two same-account real devices including an iPad")
        self.identity = {"app_id": spec.app_id, "iap_id": spec.iap_id, "product_id": spec.product_id,
                         "bundle_id": self.bundle_id, "version": spec.version, "build": spec.build,
                         "build_id": self.build_id, "version_id": self.version_id,
                         "iap_version_id": self.iap_version_id, "source_git_sha": self.sha,
                         "device_families": self.device_families,
                         "store_screenshot_locales": sorted({r.get("locale") for r in uploaded}),
                         "repo": str(spec.repo.resolve())}
        require(str(spec.receipt.resolve()) not in self.files, "receipt aliases an input image or selection plan")
        self.fingerprint = hashlib.sha256(json.dumps({"identity": self.identity, "files": self.files},
                                                     sort_keys=True).encode()).hexdigest()

    def unchanged(self) -> None:
        require(all(digest(Path(p)) == sha for p, sha in self.files.items()), "input evidence changed after preflight")


def git_contract(evidence: Evidence) -> dict[str, Any]:
    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=evidence.spec.repo, capture_output=True, text=True)
        require(result.returncode == 0, f"git contract command failed: {args[0]}")
        return result.stdout.strip()
    require(git("branch", "--show-current") == "main", "release repository is not on main")
    require(not git("status", "--porcelain", "--untracked-files=all"), "release repository is dirty")
    git("fetch", "origin", "main")
    head = git("rev-parse", "HEAD")
    tracking = git("rev-parse", "refs/remotes/origin/main")
    rows = git("ls-remote", "origin", "refs/heads/main").splitlines()
    require(len(rows) == 1 and rows[0].split()[1] == "refs/heads/main", "remote main lookup is ambiguous")
    remote = rows[0].split()[0]
    require(head == tracking == remote == evidence.sha, "HEAD/origin main/processed source SHA mismatch")
    require(git("rev-list", "--left-right", "--count", "HEAD...origin/main").split() == ["0", "0"], "source diverged")
    return {"clean": True, "branch": "main", "git_sha": head, "tracking_sha": tracking, "remote_sha": remote}


class API:
    def __init__(self) -> None:
        self.credentials = asc.load_credentials()
        self.token = ""
        self.minted = 0.0

    def auth(self) -> str:
        if not self.token or time.monotonic() - self.minted > 600:
            self.token = asc.mint_token(*self.credentials)
            self.minted = time.monotonic()
        return self.token

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return asc.api_get(self.auth(), path, params)

    def write(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        return stage.api_write(self.auth(), method, path, body)


def collection(payload: dict[str, Any], label: str) -> list[dict[str, Any]]:
    require(not payload.get("links", {}).get("next"), f"{label} is paginated; cannot prove exhaustive scope")
    data = payload.get("data")
    require(isinstance(data, list) and all(isinstance(x, dict) for x in data), f"missing {label} collection")
    ids = [r.get("id") for r in data]
    require(all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids), f"invalid {label} IDs")
    return data


def resource(payload: dict[str, Any], resource_type: str, resource_id: str) -> dict[str, Any]:
    r = payload.get("data", {})
    require(isinstance(r, dict) and r.get("type") == resource_type and r.get("id") == resource_id,
            f"resource identity mismatch: {resource_type}")
    return r


def empty_ready(r: dict[str, Any]) -> bool:
    a = r.get("attributes", {})
    items = r.get("relationships", {}).get("items", {})
    return (a.get("platform") == "IOS" and a.get("state") == "READY_FOR_REVIEW"
            and a.get("submittedDate") is None and "submittedDate" in a
            and items.get("data") == [] and items.get("meta", {}).get("paging", {}).get("total") == 0)


class Controller:
    def __init__(self, evidence: Evidence, api: Any, git_check=git_contract):
        self.e = evidence
        self.s = evidence.spec
        self.api = api
        self.git_check = git_check
        self.journal = read_json(self.s.receipt) if self.s.receipt.exists() else {
            "schema_version": 1, "controller": "autoapp-toolkit/asc_app_review_release.py", "started_at": now(),
            "fingerprint": evidence.fingerprint, "identity": evidence.identity, "input_sha256": evidence.files,
            "operations": [], "pending": None, "review_submitted": False, "public_release_claimed": False}
        require(self.journal.get("schema_version") == 1 and self.journal.get("fingerprint") == evidence.fingerprint,
                "existing receipt belongs to different release evidence")

    def persist(self) -> None:
        save(self.s.receipt, self.journal)

    def reviews(self) -> list[dict[str, Any]]:
        return collection(self.api.get(f"/v1/apps/{self.s.app_id}/reviewSubmissions", {
            "filter[platform]": "IOS", "limit": 200, "include": "app,items,appStoreVersionForReview",
            "limit[items]": 50}), "review submissions")

    def review(self, rid: str) -> dict[str, Any]:
        r = resource(self.api.get(f"/v1/reviewSubmissions/{rid}", {
            "include": "app,items,appStoreVersionForReview", "limit[items]": 50}), "reviewSubmissions", rid)
        require(asc.relationship_id(r, "app") == self.s.app_id and r.get("attributes", {}).get("platform") == "IOS",
                "review submission belongs to a foreign app/platform")
        return r

    def items(self, rid: str) -> list[dict[str, Any]]:
        return collection(self.api.get(f"/v1/reviewSubmissions/{rid}/items", {
            "limit": 200, "include": "appStoreVersion,inAppPurchaseVersion"}), "review items")

    def live_evidence(self, record_poll: bool = False) -> dict[str, Any]:
        e, s = self.e, self.s
        app = resource(self.api.get(f"/v1/apps/{s.app_id}"), "apps", s.app_id)
        require(app.get("attributes", {}).get("bundleId") == e.bundle_id, "foreign ASC app")
        versions = collection(self.api.get(f"/v1/apps/{s.app_id}/appStoreVersions", {
            "filter[platform]": "IOS", "limit": 200}), "app versions")
        matching = [v for v in versions if v.get("attributes", {}).get("versionString") == s.version]
        require(len(matching) == 1 and matching[0].get("id") == e.version_id, "app version membership mismatch")
        v = resource(self.api.get(f"/v1/appStoreVersions/{e.version_id}"), "appStoreVersions", e.version_id)
        require(v.get("attributes", {}).get("platform") == "IOS"
                and v.get("attributes", {}).get("versionString") == s.version, "app version identity mismatch")
        b = resource(self.api.get(f"/v1/appStoreVersions/{e.version_id}/build"), "builds", e.build_id)
        require(b.get("attributes", {}).get("version") == s.build
                and b.get("attributes", {}).get("processingState") == "VALID"
                and b.get("attributes", {}).get("expired") is False, "selected build is not exact VALID build")
        exact = resource(self.api.get(f"/v1/builds/{e.build_id}", {"include": "app,preReleaseVersion"}), "builds", e.build_id)
        train_id = asc.relationship_id(exact, "preReleaseVersion")
        train = resource(self.api.get(f"/v1/preReleaseVersions/{train_id}"), "preReleaseVersions", train_id or "")
        require(asc.relationship_id(exact, "app") == s.app_id
                and train.get("attributes", {}).get("version") == s.version
                and train.get("attributes", {}).get("platform") == "IOS"
                and exact.get("attributes", {}).get("version") == s.build
                and exact.get("attributes", {}).get("processingState") == "VALID"
                and exact.get("attributes", {}).get("expired") is False
                and exact.get("attributes", {}).get("usesNonExemptEncryption") is False
                and bool(exact.get("attributes", {}).get("iconAssetToken", {}).get("templateUrl")),
                "foreign/unprocessed/expired ASC build or train")
        parent = resource(self.api.get(f"/v2/inAppPurchases/{s.iap_id}"), "inAppPurchases", s.iap_id)
        require(parent.get("attributes", {}).get("state") == "APPROVED"
                and parent.get("attributes", {}).get("productId") == s.product_id
                and parent.get("attributes", {}).get("inAppPurchaseType") == "NON_CONSUMABLE", "existing approved SKU mismatch")
        parents = collection(self.api.get(f"/v1/apps/{s.app_id}/inAppPurchasesV2", {"limit": 200}), "IAP parents")
        require(sum(p.get("id") == s.iap_id for p in parents) == 1, "IAP belongs to a foreign app")
        iv = resource(self.api.get(f"/v1/inAppPurchaseVersions/{e.iap_version_id}", {
            "include": "inAppPurchase"}), "inAppPurchaseVersions", e.iap_version_id)
        require(asc.relationship_id(iv, "inAppPurchase") == s.iap_id, "IAP metadata version belongs to a foreign SKU")
        require(isinstance(iv.get("attributes", {}).get("version"), int)
                and iv["attributes"]["version"] > 1, "IAP metadata is not a new version of approved product")
        state_readback = {"observed_at": now(), "version": v, "selected_build": b, "iap_version": iv}
        self.journal["last_candidate_state_readback"] = state_readback
        if record_poll:
            rid = self.journal["review_submission_id"]
            state_readback["review_submission"] = self.review(rid)
            state_readback["items"] = self.items(rid)
            self.journal.setdefault("poll_readbacks", []).append(state_readback)
        self.persist()  # Preserve actual states even when a terminal state fails the gate.
        allowed = {"PREPARE_FOR_SUBMISSION", "READY_FOR_REVIEW"}
        if self.journal.get("review_submission_id"):
            allowed |= {"WAITING_FOR_REVIEW", "IN_REVIEW"}
        iap_allowed = allowed.copy()
        if self.journal.get("submitted_request_at"):
            iap_allowed |= {"ACCEPTED", "APPROVED"}
        app_state, iap_state = asc.state_of(v.get("attributes", {})), iv.get("attributes", {}).get("state")
        require(app_state in allowed and iap_state in iap_allowed,
                f"app/IAP version is rejected, submitted externally, or otherwise ineligible: app={app_state} IAP={iap_state}")
        if iap_state in {"ACCEPTED", "APPROVED"}:
            require(self.journal.get("submitted_request_at") is not None
                    and app_state in {"WAITING_FOR_REVIEW", "IN_REVIEW"},
                    "completed IAP metadata is not an owned submitted app review")
            rid = self.journal["review_submission_id"]
            require(self.review(rid).get("attributes", {}).get("state") in {"WAITING_FOR_REVIEW", "IN_REVIEW"},
                    "completed IAP metadata belongs to an inactive review container")
            self.exact_items(rid, complete=True)
        image = resource(self.api.get(f"/v2/inAppPurchases/{s.iap_id}/appStoreReviewScreenshot"),
                         "inAppPurchaseAppStoreReviewScreenshots", e.image["new_asset_id"])
        attrs = image.get("attributes", {})
        require(attrs.get("assetDeliveryState", {}).get("state") == "COMPLETE"
                and attrs.get("sourceFileChecksum") == digest(s.paywall_image, "md5")
                and attrs.get("fileSize") == s.paywall_image.stat().st_size, "current IAP image is not COMPLETE exact pixels")
        localizations = collection(self.api.get(f"/v1/inAppPurchaseVersions/{e.iap_version_id}/localizations", {
            "limit": 200}), "IAP localizations")
        expected = e.iap.get("localizations_readback")
        require(isinstance(expected, list) and bool(expected), "no version-based IAP localization readbacks")
        def localized_values(rows):
            return sorted((r.get("id"), r.get("attributes", {}).get("locale"), r.get("attributes", {}).get("name"),
                           r.get("attributes", {}).get("description")) for r in rows)
        require(localized_values(localizations) == localized_values(expected), "IAP metadata changed after independent review")
        readbacks = e.metadata.get("readbacks")
        require(isinstance(readbacks, list) and bool(readbacks), "no app metadata readbacks")
        current_localizations = collection(self.api.get(f"/v1/appStoreVersions/{e.version_id}/appStoreVersionLocalizations", {
            "limit": 200}), "selected app version localizations")
        require({(r.get("id"), r.get("attributes", {}).get("locale")) for r in current_localizations}
                == {(r.get("version_localization_id"), r.get("locale")) for r in readbacks},
                "app metadata/screenshot localizations do not belong to exact selected app version")
        for localization in readbacks:
            lid = localization.get("version_localization_id")
            current = resource(self.api.get(f"/v1/appStoreVersionLocalizations/{lid}"), "appStoreVersionLocalizations", lid)
            fields = localization.get("version_fields", {})
            require(fields and current.get("attributes", {}).get("locale") == localization.get("locale")
                    and all(current.get("attributes", {}).get(k) == value for k, value in fields.items()),
                    "app version metadata changed after independent review")
            info_id = localization.get("app_info_localization_id")
            info = resource(self.api.get(f"/v1/appInfoLocalizations/{info_id}"), "appInfoLocalizations", info_id)
            require(all(info.get("attributes", {}).get(k) == value for k, value in
                        localization.get("app_info_fields", {}).items()), "app information changed after independent review")
        self.live_store_images(readbacks)
        return {"version": v, "build": b, "iap_version": iv, "image": image}

    def live_store_images(self, readbacks: list[dict[str, Any]]) -> None:
        expected = self.e.store_images["uploaded"]
        actual = []
        for localization in readbacks:
            locale = localization["locale"]
            lid = localization["version_localization_id"]
            sets = collection(self.api.get(f"/v1/appStoreVersionLocalizations/{lid}/appScreenshotSets", {
                "limit": 200}), "store screenshot sets")
            for screenshot_set in sets:
                sid = screenshot_set["id"]
                display_type = screenshot_set.get("attributes", {}).get("screenshotDisplayType")
                rows = collection(self.api.get(f"/v1/appScreenshotSets/{sid}/appScreenshots", {
                    "limit": 200}), "store screenshots")
                for screenshot in rows:
                    a = screenshot.get("attributes", {})
                    require(a.get("assetDeliveryState", {}).get("state") == "COMPLETE", "store screenshot is not COMPLETE")
                    asset = a.get("imageAsset", {})
                    actual.append({"locale": locale, "display_type": display_type, "id": screenshot["id"], "set_id": sid,
                                   "source_file_checksum": a.get("sourceFileChecksum"),
                                   "dimensions": [asset.get("width"), asset.get("height")]})
        keys = ["locale", "display_type", "id", "set_id", "source_file_checksum", "dimensions"]
        def comparable(rows):
            return sorted(json.dumps({k: r.get(k) for k in keys}, sort_keys=True) for r in rows)
        require(comparable(actual) == comparable(expected), "live store screenshots differ from current reviewed exact asset mapping")

    def check_source(self) -> None:
        self.e.unchanged()
        self.journal["source_readback"] = self.git_check(self.e)

    def validate_containers(self) -> list[dict[str, Any]]:
        rows = self.reviews()
        owned = self.journal.get("review_submission_id")
        for r in rows:
            require(r.get("attributes", {}).get("platform") == "IOS"
                    and asc.relationship_id(r, "app") == self.s.app_id, "foreign review container")
            if r.get("id") == owned:
                continue
            if r.get("attributes", {}).get("state") == "COMPLETE":
                continue  # Closed history is left untouched, and never reused.
            require(empty_ready(r), "foreign nonempty, submitted, rejected, or unresolved review container blocks writes")
        if owned:
            require(sum(r.get("id") == owned for r in rows) == 1, "journal-owned review container disappeared")
        return rows

    def recover(self, operation: dict[str, Any]) -> dict[str, Any]:
        kind, context = operation["kind"], operation["context"]
        if kind == "create":
            rows = [r for r in self.reviews() if r["id"] not in context["before_ids"]
                    and empty_ready(r) and asc.relationship_id(r, "app") == self.s.app_id]
            require(len(rows) == 1, "unknown create outcome is not exactly one new empty container; do not retry")
            return rows[0]
        rid = self.journal["review_submission_id"]
        if kind.startswith("item:"):
            relationship = kind.split(":", 1)[1]
            rows = self.items(rid)
            new = [r for r in rows if r["id"] not in context["before_ids"]]
            require(len(new) == 1 and asc.relationship_id(new[0], relationship) == context["target_id"],
                    "unknown item outcome lacks exact unique target relationship; do not retry")
            other = "inAppPurchaseVersion" if relationship == "appStoreVersion" else "appStoreVersion"
            require(asc.relationship_id(new[0], other) is None, "new review item has an unexpected second target")
            return new[0]
        if kind == "release-type":
            v = resource(self.api.get(f"/v1/appStoreVersions/{self.e.version_id}"), "appStoreVersions", self.e.version_id)
            require(v.get("attributes", {}).get("releaseType") == "AFTER_APPROVAL", "unknown release-type outcome; do not retry")
            return v
        require(kind == "submit", "unknown journal operation")
        r = self.review(rid)
        require(r.get("attributes", {}).get("state") in {"WAITING_FOR_REVIEW", "IN_REVIEW"},
                "unknown submit outcome is not confirmed; do not retry")
        return r

    def resolve_pending(self) -> None:
        operation = self.journal.get("pending")
        if not operation:
            return
        result = self.recover(operation)
        acknowledged = operation.get("response_id")
        require(not acknowledged or acknowledged == result.get("id"), "write response and recovered resource disagree")
        kind = operation["kind"]
        if kind == "create":
            self.journal["review_submission_id"] = result["id"]
        elif kind.startswith("item:"):
            self.journal.setdefault("item_ids", {})[kind.split(":", 1)[1]] = result["id"]
        elif kind == "submit":
            self.journal["submitted_request_at"] = operation["started_at"]
        operation.update({"status": "confirmed_by_GET", "confirmed_at": now(), "resource_id": result["id"]})
        self.journal["operations"].append(operation)
        self.journal["pending"] = None
        self.persist()

    def mutate(self, kind: str, method: str, path: str, body: dict[str, Any], context: dict[str, Any]) -> None:
        require(self.journal.get("pending") is None, "unresolved write journal blocks another mutation")
        self.check_source()
        self.live_evidence()
        current_containers = self.validate_containers()
        self.check_source()
        selected = resource(self.api.get(f"/v1/appStoreVersions/{self.e.version_id}/build"), "builds", self.e.build_id)
        require(selected.get("attributes", {}).get("version") == self.s.build
                and selected.get("attributes", {}).get("processingState") == "VALID"
                and selected.get("attributes", {}).get("expired") is False, "selected build changed before write")
        # These reads deliberately follow the long metadata/image preflight.
        # Do not transmit based on an earlier view of the owned container.
        if kind == "create":
            require(set(r["id"] for r in current_containers) == set(context["before_ids"]),
                    "review containers changed before creation")
        else:
            rid = self.journal["review_submission_id"]
            owned = self.review(rid)
            require(owned.get("attributes", {}).get("state") == "READY_FOR_REVIEW"
                    and "submittedDate" in owned.get("attributes", {})
                    and owned["attributes"]["submittedDate"] is None, "owned review container is not never-submitted READY")
            items = self.exact_items(rid, complete=kind in {"release-type", "submit"})
            if kind.startswith("item:"):
                require(set(r["id"] for r in items) == set(context["before_ids"]), "review items changed before item creation")
            if kind == "submit":
                current_version = resource(self.api.get(f"/v1/appStoreVersions/{self.e.version_id}"),
                                           "appStoreVersions", self.e.version_id)
                current_attrs = current_version.get("attributes", {})
                require(current_attrs.get("releaseType") == "AFTER_APPROVAL"
                        and current_attrs.get("versionString") == self.s.version
                        and current_attrs.get("platform") == "IOS"
                        and asc.state_of(current_attrs) == "READY_FOR_REVIEW",
                        "exact READY iOS version or AFTER_APPROVAL release type changed before submission")
                current_iap = resource(self.api.get(f"/v1/inAppPurchaseVersions/{self.e.iap_version_id}"),
                                       "inAppPurchaseVersions", self.e.iap_version_id)
                require(current_iap.get("attributes", {}).get("state") == "READY_FOR_REVIEW",
                        "IAP metadata version is not READY at submission")
        operation = {"kind": kind, "method": method, "path": path, "body": body,
                     "context": context, "started_at": now(), "status": "pending"}
        self.journal["pending"] = operation
        self.persist()  # Durable before transmission; a process crash resumes with GET.
        try:
            response = self.api.write(method, path, body)
            operation["response_id"] = (response or {}).get("data", {}).get("id")
            operation["status"] = "response_received"
        except (stage.StageError, asc.ASCError, OSError, TimeoutError) as error:
            operation["status"] = "outcome_unknown"
            operation["error_type"] = type(error).__name__  # No token/raw response/user data in log.
        self.persist()
        self.resolve_pending()

    def exact_items(self, rid: str, complete: bool) -> list[dict[str, Any]]:
        rows = self.items(rid)
        known = self.journal.get("item_ids", {})
        require(set(r["id"] for r in rows) == set(known.values()), "foreign/unowned review item blocks writes")
        targets = {"appStoreVersion": self.e.version_id, "inAppPurchaseVersion": self.e.iap_version_id}
        require(set(known) <= set(targets), "journal has unsupported review targets")
        for relationship, item_id in known.items():
            item = next(r for r in rows if r["id"] == item_id)
            other = next(k for k in targets if k != relationship)
            require(asc.relationship_id(item, relationship) == targets[relationship]
                    and asc.relationship_id(item, other) is None, "review item target changed")
            require(item.get("attributes", {}).get("state") in {"READY_FOR_REVIEW", "ACCEPTED", "APPROVED"},
                    "review item is rejected or removed")
        if complete:
            require(len(rows) == 2 and set(known) == set(targets), "submission requires exactly app + new IAP metadata items")
            require(asc.relationship_id(self.review(rid), "appStoreVersionForReview") == self.e.version_id,
                    "review container points to another app version")
        return rows

    def run(self, submit: bool = False, confirm: str | None = None, poll_seconds: int = 90) -> dict[str, Any]:
        try:
            return self._run(submit, confirm, poll_seconds)
        except (ReleaseError, asc.ASCError, stage.StageError, OSError) as error:
            self.journal["last_failure"] = {"observed_at": now(), "type": type(error).__name__, "message": str(error)}
            self.persist()
            raise

    def _run(self, submit: bool, confirm: str | None, poll_seconds: int) -> dict[str, Any]:
        self.check_source()
        if self.journal.get("pending"):
            self.resolve_pending()  # GET only, even in default read-only mode.
        live = self.live_evidence()
        rows = self.validate_containers()
        rid = self.journal.get("review_submission_id")
        if rid:
            self.exact_items(rid, complete=bool(self.journal.get("submitted_request_at")))
        self.journal.update({"last_preflight_at": now(), "preflight_passed": True,
                             "read_only_invocation": not submit})
        self.persist()
        if not submit:
            return self.journal
        require(confirm == f"submit-{self.e.name.lower()}-{self.s.version}", "exact submit confirmation missing")
        if not rid:
            candidates = sorted((r for r in rows if empty_ready(r)), key=lambda r: r["id"])
            if candidates:
                self.journal["review_submission_id"] = candidates[0]["id"]
                self.journal["never_submitted_empty_container_reused"] = True
                self.persist()
            else:
                self.mutate("create", "POST", "/v1/reviewSubmissions", {"data": {
                    "type": "reviewSubmissions", "attributes": {"platform": "IOS"},
                    "relationships": {"app": {"data": {"type": "apps", "id": self.s.app_id}}}}},
                    {"before_ids": [r["id"] for r in rows]})
            rid = self.journal["review_submission_id"]
        r = self.review(rid)
        if r.get("attributes", {}).get("state") == "READY_FOR_REVIEW":
            require(r.get("attributes", {}).get("submittedDate") is None, "previously submitted container cannot be reused")
            for relationship, resource_type, target in [("appStoreVersion", "appStoreVersions", self.e.version_id),
                    ("inAppPurchaseVersion", "inAppPurchaseVersions", self.e.iap_version_id)]:
                existing = self.exact_items(rid, complete=False)
                if relationship not in self.journal.get("item_ids", {}):
                    self.mutate(f"item:{relationship}", "POST", "/v1/reviewSubmissionItems", {"data": {
                        "type": "reviewSubmissionItems", "relationships": {
                            "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": rid}},
                            relationship: {"data": {"type": resource_type, "id": target}}}}},
                        {"before_ids": [i["id"] for i in existing], "target_id": target})
            exact_items = self.exact_items(rid, complete=True)
            self.journal["exact_two_items_before_submit"] = exact_items
            self.persist()
            live = self.live_evidence()
            if live["version"].get("attributes", {}).get("releaseType") != "AFTER_APPROVAL":
                self.mutate("release-type", "PATCH", f"/v1/appStoreVersions/{self.e.version_id}", {"data": {
                    "type": "appStoreVersions", "id": self.e.version_id,
                    "attributes": {"releaseType": "AFTER_APPROVAL"}}}, {})
            self.exact_items(rid, complete=True)
            require(self.review(rid).get("attributes", {}).get("state") == "READY_FOR_REVIEW", "review state changed before submit")
            self.mutate("submit", "PATCH", f"/v1/reviewSubmissions/{rid}", {"data": {
                "type": "reviewSubmissions", "id": rid, "attributes": {"submitted": True}}}, {})
        else:
            require(self.journal.get("submitted_request_at") is not None
                    and r.get("attributes", {}).get("state") in {"WAITING_FOR_REVIEW", "IN_REVIEW"},
                    "journal container is not an owned confirmed submission")
        deadline = time.monotonic() + poll_seconds
        while True:
            live = self.live_evidence(record_poll=True)
            r = self.journal["poll_readbacks"][-1]["review_submission"]
            self.exact_items(rid, complete=True)
            require(live["version"].get("attributes", {}).get("releaseType") == "AFTER_APPROVAL", "release type changed")
            review_state = r.get("attributes", {}).get("state")
            require(review_state in {"READY_FOR_REVIEW", "WAITING_FOR_REVIEW", "IN_REVIEW"},
                    f"review submission entered an ineligible state: {review_state}")
            if (r.get("attributes", {}).get("state") in {"WAITING_FOR_REVIEW", "IN_REVIEW"}
                    and asc.state_of(live["version"].get("attributes", {})) in {"WAITING_FOR_REVIEW", "IN_REVIEW"}
                    and live["iap_version"].get("attributes", {}).get("state") in {
                        "WAITING_FOR_REVIEW", "IN_REVIEW", "ACCEPTED", "APPROVED"}):
                break
            require(time.monotonic() < deadline, "exact app + IAP review acceptance is not confirmed; preserve journal")
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        self.journal.update({"confirmed_at": now(), "review_submitted": True, "public_release_claimed": False,
                             "review_readback": r, "version_readback": live["version"],
                             "iap_version_readback": live["iap_version"], "selected_build_readback": live["build"],
                             "physical_test_proved": self.e.proof.get("physical_release_gate_completed") is True,
                             "iap_review_completed": live["iap_version"].get("attributes", {}).get("state") == "APPROVED"})
        if self.journal.get("last_failure"):
            self.journal["last_failure"]["resolved_by_confirmation_at"] = self.journal["confirmed_at"]
        self.persist()
        return self.journal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["repo", "release-receipt", "metadata-receipt", "iap-metadata-receipt", "iap-image-receipt",
                 "paywall-image", "store-image-receipt", "stage-receipt", "independent-preflight", "receipt"]:
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ["app-id", "iap-id", "product-id", "version", "build"]:
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--submit", action="store_true", help="perform journaled app + IAP review submission")
    parser.add_argument("--confirm", help="exact submit-<lowercase app name>-<version> token")
    args = parser.parse_args(argv)
    values = vars(args).copy()
    submit, confirm = values.pop("submit"), values.pop("confirm")
    try:
        spec = Spec(**values)
        evidence = Evidence(spec)
        spec.receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = spec.receipt.with_name(spec.receipt.name + ".lock")
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ReleaseError("another controller owns this receipt lock") from error
            result = Controller(evidence, API()).run(submit, confirm)
        state = "APP_REVIEW_CONFIRMED" if result.get("review_submitted") else "READ_ONLY_PREFLIGHT_OK"
        print(f"{evidence.name} {state} {spec.version}/{spec.build} public_release=false")
        return 0
    except (ReleaseError, asc.ASCError, stage.StageError, OSError, ValueError, KeyError) as error:
        print(f"APP_REVIEW_RELEASE_BLOCKED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
