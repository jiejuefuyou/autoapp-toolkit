"""Offline contracts for exact app + version-based IAP review submission.

The fake ASC server applies real resource transitions and can lose responses.
No credentials, account writes, simulator, or physical device are used.
"""
import copy
import hashlib
import io
import json
import plistlib
import subprocess
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import asc_app_review_release as release


SHA = "a" * 40


def rel(kind, identifier):
    return {"data": {"type": kind, "id": identifier}}


def obj(kind, identifier, attrs=None, relationships=None):
    return {"type": kind, "id": identifier, "attributes": attrs or {}, "relationships": relationships or {}}


class Fixture:
    def __init__(self, root, name="AutoChoice", bundle="com.jiejuefuyou.autochoice", families=None):
        families = families or [1, 2]
        self.root = root
        self.app = obj("apps", "app", {"bundleId": bundle})
        self.version = obj("appStoreVersions", "version", {
            "platform": "IOS", "versionString": "1.0.23", "appStoreState": "PREPARE_FOR_SUBMISSION", "releaseType": "MANUAL"})
        self.build = obj("builds", "build", {"version": "38", "processingState": "VALID", "expired": False,
            "usesNonExemptEncryption": False, "iconAssetToken": {"templateUrl": "icon"}},
            {"app": rel("apps", "app"), "preReleaseVersion": rel("preReleaseVersions", "train")})
        self.train = obj("preReleaseVersions", "train", {"version": "1.0.23", "platform": "IOS"})
        self.product = bundle + ".premium"
        self.parent = obj("inAppPurchases", "iap", {"state": "APPROVED", "productId": self.product,
                                                    "inAppPurchaseType": "NON_CONSUMABLE"})
        self.iap_version = obj("inAppPurchaseVersions", "iap-version", {"version": 2, "state": "PREPARE_FOR_SUBMISSION"},
                              {"inAppPurchase": rel("inAppPurchases", "iap")})
        self.iap_loc = obj("inAppPurchaseLocalizations", "iap-loc", {
            "locale": "en-US", "name": name + " Premium", "description": "Unlock more features."})
        self.version_loc = obj("appStoreVersionLocalizations", "version-loc", {
            "locale": "en-US", "description": "Current truthful description", "whatsNew": "Current fixes"})
        self.info_loc = obj("appInfoLocalizations", "info-loc", {"name": name, "privacyPolicyUrl": "https://example.com/privacy"})
        self.version_locs = {}
        self.info_locs = {}
        self.store_sets = {}
        self.store_assets = {}
        image_path = root / "paywall.png"
        image_path.write_bytes(b"exact current test image bytes")
        self.image = obj("inAppPurchaseAppStoreReviewScreenshots", "image", {"fileSize": image_path.stat().st_size,
            "sourceFileChecksum": release.digest(image_path, "md5"), "assetDeliveryState": {"state": "COMPLETE"}})
        visual = root / "iap-visual.json"
        visual.write_text(json.dumps({"visual_review_completed": True, "sha256": release.digest(image_path)}))
        source = {"git_sha": SHA, "remote_sha": SHA, "tracking_sha": SHA, "branch": "main", "clean": True, "ahead": 0, "behind": 0}
        identity = {"bundle_id": bundle, "version": "1.0.23", "build": "38"}
        ipa_path = root / "candidate.ipa"
        with zipfile.ZipFile(ipa_path, "w") as archive:
            archive.writestr(zipfile.ZipInfo("Payload/Candidate.app/Info.plist", date_time=(2026, 1, 1, 0, 0, 0)),
                plistlib.dumps({"CFBundleIdentifier": bundle,
                "CFBundleShortVersionString": "1.0.23", "CFBundleVersion": "38", "UIDeviceFamily": families}))
        documents = {
            "release": {"stage": "testflight-processed", "app": dict(identity, name=name),
                        "artifact": dict(identity, ipa_path=str(ipa_path), ipa_sha256=release.digest(ipa_path), codesign="passed"),
                        "source": source, "testflight": {"id": "build"},
                        "claims": {"testflight_processed": True, "source_remote_readback": True, "signed_artifact_verified": True}},
            "metadata": {"draft_metadata_applied": True, "source_git_sha": SHA, "app_id": "app", "build_id": "build",
                "version_id": "version", "version": "1.0.23", "build": "38", "readbacks": [{
                    "locale": "en-US", "version_localization_id": "version-loc", "app_info_localization_id": "info-loc",
                    "version_fields": {"description": "Current truthful description", "whatsNew": "Current fixes"},
                    "app_info_fields": {"name": name, "privacyPolicyUrl": "https://example.com/privacy"}}]},
            "iap-metadata": {"iap_metadata_applied": True, "iap_id": "iap", "iap_metadata_version_id": "iap-version",
                "parent_product_id_unchanged": self.product, "price_writes_performed": False,
                "iap_metadata_version_readback": self.iap_version, "localizations_readback": [self.iap_loc]},
            "iap-image": {"complete": True, "parent_product_id": self.product, "price_writes_performed": False,
                "current_paywall_sha256": release.digest(image_path), "new_asset_id": "image", "readback": self.image,
                "capture_and_visual_receipt": str(visual)},
            "stage": {"staged_ok": True, "source_git_sha": SHA, "app": {"id": "app", "bundle_id": bundle},
                "version": {"id": "version", "version": "1.0.23", "selected_build": {"id": "build"}},
                "checks": [{"name": "stage", "ok": True}]},
            "proof": {"ready_for_submission": True, "independent_reviewer_no_mutations": True, "remaining_blockers": [],
                "app_id": "app", "bundle_id": bundle, "version": "1.0.23", "build": "38", "build_id": "build",
                "source_git_sha": SHA, "app_store_version_id": "version", "new_iap_metadata_version_id": "iap-version",
                "checks": [{"name": "independent", "pass": True}]},
        }
        selected, uploaded = [], []
        locales = ["en-US", "ja", "zh-Hans", "zh-Hant", "ko", "es-ES", "fr-FR", "de-DE"]
        readbacks = documents["metadata"]["readbacks"]
        for locale in locales:
            lid = "version-loc" if locale == "en-US" else "version-loc-" + locale
            iid = "info-loc" if locale == "en-US" else "info-loc-" + locale
            self.version_locs[lid] = self.version_loc if locale == "en-US" else copy.deepcopy(self.version_loc)
            self.version_locs[lid].update(id=lid)
            self.version_locs[lid]["attributes"]["locale"] = locale
            self.info_locs[iid] = self.info_loc if locale == "en-US" else copy.deepcopy(self.info_loc)
            self.info_locs[iid].update(id=iid)
            if locale != "en-US":
                readbacks.append(dict(readbacks[0], locale=locale, version_localization_id=lid, app_info_localization_id=iid))
            self.store_sets[lid] = []
            for display, dimensions in [("APP_IPHONE_67", [1320, 2868])] + (
                    [("APP_IPAD_PRO_3GEN_129", [2064, 2752])] if locale == "en-US" and 2 in families else []):
                sid, aid = "set-" + locale + display, "asset-" + locale + display
                screenshot_path = root / (aid + ".png")
                screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", *dimensions) + b"fixture")
                checksum = release.digest(screenshot_path, "md5")
                sha = release.digest(screenshot_path)
                self.store_sets[lid].append(obj("appScreenshotSets", sid, {"screenshotDisplayType": display}))
                self.store_assets[sid] = [obj("appScreenshots", aid, {"sourceFileChecksum": checksum,
                    "imageAsset": {"width": dimensions[0], "height": dimensions[1]}, "assetDeliveryState": {"state": "COMPLETE"}})]
                selected.append({"locale": locale, "display_type": display, "path": str(screenshot_path), "sha256": sha, "dimensions": dimensions})
                uploaded.append({"locale": locale, "display_type": display, "id": aid, "set_id": sid,
                    "source_file_checksum": checksum, "source_sha256": sha, "dimensions": dimensions})
        plan = root / "store-plan.json"
        plan.write_text(json.dumps({"visual_review_completed": True, "source_git_sha": SHA, "requires_ipad": 2 in families, "images": selected}))
        documents["store-image"] = {"screenshots_complete": True, "source_git_sha": SHA, "requires_ipad": 2 in families,
                                    "selected_plan": str(plan), "uploaded": uploaded}
        self.paths = {}
        for key, value in documents.items():
            path = root / (key + ".json")
            path.write_text(json.dumps(value))
            self.paths[key] = path
        self.spec = release.Spec(root, "app", "iap", self.product, "1.0.23", "38", self.paths["release"],
            self.paths["metadata"], self.paths["iap-metadata"], self.paths["iap-image"], image_path,
            self.paths["store-image"], self.paths["stage"], self.paths["proof"], root / "submission.json")

    def edit(self, key, fn):
        p = release.read_json(self.paths[key])
        fn(p)
        self.paths[key].write_text(json.dumps(p))


class FakeASC:
    def __init__(self, fixture):
        self.f = fixture
        self.containers = {}
        self.review_items = {}
        self.writes = []
        self.gets = []
        self.lose = set()
        self.crash = set()
        self.no_apply = set()
        self.ambiguous_create = False
        self.wrong_item_target = False
        self.selected_build = fixture.build
        self.paginated = False
        self.iap_states_after_submit = []
        self.submission_transmitted = False

    def add_container(self, identifier="empty", state="READY_FOR_REVIEW", submitted=None, item_ids=None):
        items = item_ids or []
        r = obj("reviewSubmissions", identifier, {"platform": "IOS", "state": state, "submittedDate": submitted}, {
            "app": rel("apps", "app"), "items": {"data": [{"type": "reviewSubmissionItems", "id": x} for x in items],
                                                  "meta": {"paging": {"total": len(items)}}},
            "appStoreVersionForReview": {"data": None}})
        self.containers[identifier] = r
        self.review_items[identifier] = []
        return r

    def get(self, path, params=None):
        self.gets.append((path, params))
        f = self.f
        if path == "/v1/inAppPurchaseVersions/iap-version" and self.submission_transmitted and self.iap_states_after_submit:
            f.iap_version["attributes"]["state"] = self.iap_states_after_submit.pop(0)
        table = {
            "/v1/apps/app": f.app, "/v1/appStoreVersions/version": f.version,
            "/v1/appStoreVersions/version/build": self.selected_build, "/v1/builds/build": f.build,
            "/v1/preReleaseVersions/train": f.train, "/v2/inAppPurchases/iap": f.parent,
            "/v1/inAppPurchaseVersions/iap-version": f.iap_version,
            "/v2/inAppPurchases/iap/appStoreReviewScreenshot": f.image,
            "/v1/appStoreVersionLocalizations/version-loc": f.version_loc,
            "/v1/appInfoLocalizations/info-loc": f.info_loc,
            "/v1/apps/app/appStoreVersions": [f.version], "/v1/apps/app/inAppPurchasesV2": [f.parent],
            "/v1/appStoreVersions/version/appStoreVersionLocalizations": list(f.version_locs.values()),
            "/v1/inAppPurchaseVersions/iap-version/localizations": [f.iap_loc],
            "/v1/apps/app/reviewSubmissions": list(self.containers.values()),
        }
        if path in table:
            data = table[path]
        elif path.startswith("/v1/appStoreVersionLocalizations/"):
            lid = path.split("/")[3]
            data = f.store_sets[lid] if path.endswith("/appScreenshotSets") else f.version_locs[lid]
        elif path.startswith("/v1/appInfoLocalizations/"):
            data = f.info_locs[path.split("/")[3]]
        elif path.startswith("/v1/appScreenshotSets/"):
            data = f.store_assets[path.split("/")[3]]
        elif path.startswith("/v1/reviewSubmissions/"):
            identifier = path.split("/")[3]
            data = self.review_items[identifier] if path.endswith("/items") else self.containers[identifier]
        else:
            raise AssertionError("unimplemented fake ASC read: " + path)
        result = {"data": copy.deepcopy(data)}
        if self.paginated and path == "/v1/apps/app/reviewSubmissions":
            result["links"] = {"next": "next-page"}
        return result

    def write(self, method, path, body):
        self.writes.append((method, path, copy.deepcopy(body)))
        data = body["data"]
        kind = "create" if path == "/v1/reviewSubmissions" else "item" if path == "/v1/reviewSubmissionItems" else (
            "release-type" if path.startswith("/v1/appStoreVersions/") else "submit")
        result = {"data": {"type": data["type"], "id": data.get("id", "lost")}}
        if kind not in self.no_apply:
            if kind == "create":
                result = {"data": self.add_container("created")}
                if self.ambiguous_create:
                    self.add_container("also-created")
            elif kind == "item":
                relationships = data["relationships"]
                rid = relationships["reviewSubmission"]["data"]["id"]
                target = next(x for x in relationships if x != "reviewSubmission")
                item_id = "item-" + target
                relation = copy.deepcopy(relationships[target])
                if self.wrong_item_target:
                    relation["data"]["id"] = "foreign-target"
                item = obj("reviewSubmissionItems", item_id, {"state": "READY_FOR_REVIEW"}, {target: relation})
                self.review_items[rid].append(item)
                container = self.containers[rid]
                container["relationships"]["items"]["data"].append({"type": "reviewSubmissionItems", "id": item_id})
                container["relationships"]["items"]["meta"]["paging"]["total"] += 1
                if target == "appStoreVersion":
                    container["relationships"]["appStoreVersionForReview"] = relation
                    self.f.version["attributes"]["appStoreState"] = "READY_FOR_REVIEW"
                else:
                    self.f.iap_version["attributes"]["state"] = "READY_FOR_REVIEW"
                result = {"data": item}
            elif kind == "release-type":
                assert method == "PATCH" and path == "/v1/appStoreVersions/version"
                assert data["type"] == "appStoreVersions" and data["id"] == "version"
                assert set(data["attributes"]) == {"releaseType"}
                assert data["attributes"]["releaseType"] in {"MANUAL", "AFTER_APPROVAL"}
                self.f.version["attributes"]["releaseType"] = data["attributes"]["releaseType"]
                result = {"data": self.f.version}
            elif kind == "submit":
                self.submission_transmitted = True
                rid = path.split("/")[3]
                self.containers[rid]["attributes"].update(state="WAITING_FOR_REVIEW", submittedDate="submitted")
                self.f.version["attributes"]["appStoreState"] = "WAITING_FOR_REVIEW"
                self.f.iap_version["attributes"]["state"] = "WAITING_FOR_REVIEW"
                result = {"data": self.containers[rid]}
        if kind in self.crash:
            raise SystemExit("simulated process termination after server accepted request")
        if kind in self.lose or kind in self.no_apply:
            raise TimeoutError("simulated response loss")
        return copy.deepcopy(result)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.f = Fixture(self.root)
        self.api = FakeASC(self.f)
        self.source_checks = 0

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, e):
        self.source_checks += 1
        return {"git_sha": e.sha, "remote_sha": e.sha, "clean": True}

    def controller(self):
        return release.Controller(release.Evidence(self.f.spec), self.api, self.git)

    def submit(self):
        return self.controller().run(True, "submit-autochoice-1.0.23", poll_seconds=0)

    def test_read_only_does_not_claim_release_or_write(self):
        result = self.controller().run()
        self.assertTrue(result["preflight_passed"])
        self.assertFalse(result["review_submitted"])
        self.assertFalse(result["public_release_claimed"])
        self.assertEqual(self.api.writes, [])

    def test_exact_confirmation_required(self):
        with self.assertRaisesRegex(release.ReleaseError, "confirmation"):
            self.controller().run(True, "wrong")
        self.assertEqual(self.api.writes, [])

    def test_create_exact_two_items_and_after_approval_and_replay(self):
        result = self.submit()
        self.assertTrue(result["review_submitted"])
        self.assertFalse(result["public_release_claimed"])
        self.assertEqual(set(result["item_ids"]), {"appStoreVersion", "inAppPurchaseVersion"})
        self.assertEqual(len(self.api.writes), 5)
        self.assertEqual(self.api.writes[-2][2]["data"]["attributes"], {"releaseType": "AFTER_APPROVAL"})
        self.assertEqual(self.api.writes[-1][2]["data"]["attributes"], {"submitted": True})
        self.assertGreaterEqual(self.source_checks, len(self.api.writes) + 1)
        self.submit()
        self.assertEqual(len(self.api.writes), 5, "a confirmed journal must not submit twice")
        self.assertEqual(self.f.spec.receipt.stat().st_mode & 0o777, 0o600)

    def test_reuse_only_empty_never_submitted_ready_container(self):
        self.api.add_container("ready-b")
        self.api.add_container("ready-a")
        result = self.submit()
        self.assertEqual(result["review_submission_id"], "ready-a")
        self.assertTrue(result["never_submitted_empty_container_reused"])
        self.assertFalse(any(p == "/v1/reviewSubmissions" for _, p, _ in self.api.writes))
        self.assertTrue(release.empty_ready(self.api.containers["ready-b"]))

    def test_foreign_nonempty_ready_blocks_every_write(self):
        self.api.add_container("foreign", item_ids=["foreign-item"])
        with self.assertRaisesRegex(release.ReleaseError, "nonempty"):
            self.submit()
        self.assertEqual(self.api.writes, [])

    def test_submitted_or_rejected_containers_are_never_reused(self):
        for state, date in [("READY_FOR_REVIEW", "previously-submitted"), ("WAITING_FOR_REVIEW", "submitted"),
                            ("UNRESOLVED_ISSUES", "submitted"), ("REJECTED", None)]:
            with self.subTest(state=state):
                self.api.containers.clear()
                self.api.add_container(state=state, submitted=date)
                with self.assertRaisesRegex(release.ReleaseError, "container"):
                    self.submit()
                self.assertEqual(self.api.writes, [])

    def test_complete_history_is_left_untouched(self):
        old = self.api.add_container("history", "COMPLETE", "old", ["old-item"])
        before = copy.deepcopy(old)
        self.submit()
        self.assertEqual(before, self.api.containers["history"])

    def test_missing_physical_proof_and_truthy_strings_fail_closed(self):
        for name, bundle in [("WaterNow", "com.jiejuefuyou.waternow"), ("PromptVault", "com.jiejuefuyou.promptvault")]:
            with self.subTest(name=name):
                self.f = Fixture(self.root, name, bundle)
                with self.assertRaisesRegex(release.ReleaseError, "physical"):
                    release.Evidence(self.f.spec)
                self.f.edit("proof", lambda p: p.update(physical_release_gate_completed="true",
                                                       exact_processed_build_installed_on_physical_device=True))
                with self.assertRaisesRegex(release.ReleaseError, "physical"):
                    release.Evidence(self.f.spec)
        self.assertEqual(self.api.writes, [])

    def test_prompt_real_ipad_remains_mandatory_even_with_phone_proof(self):
        self.f = Fixture(self.root, "PromptVault", "com.jiejuefuyou.promptvault")
        self.f.edit("proof", lambda p: p.update(physical_release_gate_completed=True,
            exact_processed_build_installed_on_physical_device=True))
        with self.assertRaisesRegex(release.ReleaseError, "iPad"):
            release.Evidence(self.f.spec)
        self.f.edit("proof", lambda p: p.update(two_same_account_real_devices_including_ipad=True))
        release.Evidence(self.f.spec)

    def test_foreign_or_invalid_selected_build_blocks_writes(self):
        changes = [lambda f: f.build["relationships"].update(app=rel("apps", "foreign")),
                   lambda f: f.train["attributes"].update(version="9.9.9"),
                   lambda f: f.build["attributes"].update(processingState="PROCESSING"),
                   lambda f: f.build["attributes"].update(expired=True),
                   lambda f: setattr(self.api, "selected_build", obj("builds", "foreign", f.build["attributes"]))]
        for change in changes:
            with self.subTest(change=change):
                self.f = Fixture(self.root)
                self.api = FakeASC(self.f)
                change(self.f)
                with self.assertRaises(release.ReleaseError):
                    self.submit()
                self.assertEqual(self.api.writes, [])

    def test_iap_parent_sku_and_new_metadata_membership(self):
        for change in [lambda f: f.parent["attributes"].update(productId="foreign.sku"),
                       lambda f: f.parent["attributes"].update(state="READY_TO_SUBMIT"),
                       lambda f: f.iap_version["relationships"].update(inAppPurchase=rel("inAppPurchases", "foreign")),
                       lambda f: f.iap_version["attributes"].update(version=1),
                       lambda f: f.iap_version["attributes"].update(state="APPROVED"),
                       lambda f: f.iap_version["attributes"].update(state="REJECTED")]:
            self.f = Fixture(self.root)
            self.api = FakeASC(self.f)
            change(self.f)
            with self.assertRaises(release.ReleaseError):
                self.submit()
            self.assertEqual(self.api.writes, [])

    def test_noncomplete_or_stale_image_and_metadata_fail(self):
        for change in [lambda f: f.image["attributes"].update(assetDeliveryState={"state": "UPLOADING"}),
                       lambda f: f.image["attributes"].update(sourceFileChecksum="wrong"),
                       lambda f: f.iap_loc["attributes"].update(description="changed"),
                       lambda f: f.version_loc["attributes"].update(description="changed")]:
            self.f = Fixture(self.root)
            self.api = FakeASC(self.f)
            change(self.f)
            with self.assertRaises(release.ReleaseError):
                self.submit()
            self.assertEqual(self.api.writes, [])

    def test_deleted_foreign_or_processing_store_screenshot_blocks_first_write(self):
        first_set = next(iter(self.f.store_assets))
        for change in [lambda f: f.store_assets[first_set].clear(),
                       lambda f: f.store_assets[first_set][0].update(id="foreign-asset"),
                       lambda f: f.store_assets[first_set][0]["attributes"].update(sourceFileChecksum="foreign-pixels"),
                       lambda f: f.store_assets[first_set][0]["attributes"].update(imageAsset={"width": 9, "height": 9}),
                       lambda f: f.store_assets[first_set][0]["attributes"].update(assetDeliveryState={"state": "UPLOADING"})]:
            self.f = Fixture(self.root)
            self.api = FakeASC(self.f)
            change(self.f)
            with self.assertRaisesRegex(release.ReleaseError, "screenshot"):
                self.submit()
            self.assertEqual(self.api.writes, [])

    def limit_screenshots_to(self, locales):
        receipt = release.read_json(self.f.paths["store-image"])
        receipt["uploaded"] = [r for r in receipt["uploaded"] if r["locale"] in locales]
        self.f.paths["store-image"].write_text(json.dumps(receipt))
        plan_path = self.root / "store-plan.json"
        plan = release.read_json(plan_path)
        plan["images"] = [r for r in plan["images"] if r["locale"] in locales]
        plan_path.write_text(json.dumps(plan))
        for lid in self.f.store_sets:
            if self.f.version_locs[lid]["attributes"]["locale"] not in locales:
                self.f.store_sets[lid] = []

    def test_primary_english_phone_fallback_is_valid_for_phone_only_app(self):
        self.f = Fixture(self.root, "HabitHash", "com.jiejuefuyou.habithash", [1])
        self.api = FakeASC(self.f)
        self.limit_screenshots_to({"en-US"})
        result = self.controller().run(True, "submit-habithash-1.0.23", poll_seconds=0)
        self.assertTrue(result["review_submitted"])
        self.assertEqual(result["identity"]["store_screenshot_locales"], ["en-US"])

    def test_missing_primary_english_phone_screenshot_is_rejected(self):
        self.limit_screenshots_to({"ja"})
        with self.assertRaisesRegex(release.ReleaseError, "primary English phone"):
            release.Evidence(self.f.spec)

    def test_actual_signed_device_families_require_primary_pad(self):
        self.f.edit("store-image", lambda p: p.update(requires_ipad=False))
        with self.assertRaisesRegex(release.ReleaseError, "device families"):
            release.Evidence(self.f.spec)

    def test_changed_processed_ipa_is_rejected(self):
        (self.root / "candidate.ipa").write_bytes(b"changed artifact")
        with self.assertRaisesRegex(release.ReleaseError, "signed IPA"):
            release.Evidence(self.f.spec)

    def test_selected_app_version_foreign_localization_scope_blocks_writes(self):
        self.f.version_locs["version-loc"]["attributes"]["locale"] = "foreign-locale"
        with self.assertRaisesRegex(release.ReleaseError, "localizations"):
            self.submit()
        self.assertEqual(self.api.writes, [])

    def test_null_or_failed_mixed_checks_are_not_success(self):
        for values in [{"pass": None, "ok": True}, {"pass": False, "ok": True}, {"pass": "true"}, {}]:
            self.f.edit("proof", lambda p: p.update(checks=[dict(name="uncertain", **values)]))
            with self.assertRaisesRegex(release.ReleaseError, "check"):
                release.Evidence(self.f.spec)

    def test_paywall_without_exact_visual_review_is_rejected(self):
        p = self.root / "iap-visual.json"
        p.write_text(json.dumps({"visual_review_completed": False, "sha256": release.digest(self.f.spec.paywall_image)}))
        with self.assertRaisesRegex(release.ReleaseError, "visual review"):
            release.Evidence(self.f.spec)

    def owned_prepared_controller(self):
        controller = self.controller()
        r = self.api.add_container("owned")
        controller.journal["review_submission_id"] = "owned"
        controller.journal["item_ids"] = {"appStoreVersion": "app-item", "inAppPurchaseVersion": "iap-item"}
        self.api.review_items["owned"] = [obj("reviewSubmissionItems", "app-item", {"state": "READY_FOR_REVIEW"},
                    {"appStoreVersion": rel("appStoreVersions", "version")}),
                obj("reviewSubmissionItems", "iap-item", {"state": "READY_FOR_REVIEW"},
                    {"inAppPurchaseVersion": rel("inAppPurchaseVersions", "iap-version")})]
        r["relationships"]["items"] = {"data": [{"type": "reviewSubmissionItems", "id": i} for i in ["app-item", "iap-item"]],
                                         "meta": {"paging": {"total": 2}}}
        r["relationships"]["appStoreVersionForReview"] = rel("appStoreVersions", "version")
        self.f.version["attributes"].update(releaseType="AFTER_APPROVAL", appStoreState="READY_FOR_REVIEW")
        self.f.iap_version["attributes"]["state"] = "READY_FOR_REVIEW"
        return controller

    def test_late_concurrent_manual_release_foreign_item_or_state_change_refuses_submit(self):
        changes = [lambda: self.f.version["attributes"].update(releaseType="MANUAL"),
                   lambda: self.f.version["attributes"].update(versionString="9.9.9"),
                   lambda: self.f.version["attributes"].update(platform="MAC_OS"),
                   lambda: self.f.version["attributes"].update(appStoreState="REJECTED"),
                   lambda: self.api.review_items["owned"].append(obj("reviewSubmissionItems", "foreign-item")),
                   lambda: self.api.containers["owned"]["attributes"].update(state="UNRESOLVED_ISSUES"),
                   lambda: self.api.containers["owned"]["attributes"].update(submittedDate="old"),
                   lambda: setattr(self.api, "selected_build", obj("builds", "foreign-build")),
                   lambda: self.f.iap_version["attributes"].update(state="REJECTED")]
        for change in changes:
            self.f = Fixture(self.root)
            self.api = FakeASC(self.f)
            controller = self.owned_prepared_controller()
            get = self.api.get
            def late_get(path, params=None):
                payload = get(path, params)
                if path == "/v1/appInfoLocalizations/info-loc-de-DE":
                    change()  # After the earlier version/build GET in the long metadata proof.
                return payload
            self.api.get = late_get
            with self.assertRaises(release.ReleaseError):
                controller.mutate("submit", "PATCH", "/v1/reviewSubmissions/owned", {"data": {
                    "type": "reviewSubmissions", "id": "owned", "attributes": {"submitted": True}}}, {})
            self.assertEqual(self.api.writes, [], "late change must be detected before transmission")

    def test_late_foreign_item_refuses_item_addition(self):
        self.api.add_container("owned")
        controller = self.controller()
        controller.journal["review_submission_id"] = "owned"
        get = self.api.get
        def late_get(path, params=None):
            payload = get(path, params)
            if path == "/v1/appInfoLocalizations/info-loc-de-DE":
                self.api.review_items["owned"].append(obj("reviewSubmissionItems", "foreign-item"))
            return payload
        self.api.get = late_get
        with self.assertRaisesRegex(release.ReleaseError, "foreign/unowned"):
            controller.mutate("item:appStoreVersion", "POST", "/v1/reviewSubmissionItems", {},
                              {"before_ids": [], "target_id": "version"})
        self.assertEqual(self.api.writes, [])

    def test_unknown_create_recovers_original_resource_without_retry(self):
        self.api.lose.add("create")
        result = self.submit()
        self.assertEqual(result["review_submission_id"], "created")
        self.assertEqual(sum(path == "/v1/reviewSubmissions" for _, path, _ in self.api.writes), 1)
        self.assertEqual(result["operations"][0]["status"], "confirmed_by_GET")

    def test_process_death_after_create_resumes_get_not_post(self):
        self.api.crash.add("create")
        with self.assertRaises(SystemExit):
            self.submit()
        self.assertEqual(release.read_json(self.f.spec.receipt)["pending"]["kind"], "create")
        self.api.crash.clear()
        result = self.submit()
        self.assertEqual(result["review_submission_id"], "created")
        self.assertEqual(sum(path == "/v1/reviewSubmissions" for _, path, _ in self.api.writes), 1)

    def test_ambiguous_create_outcome_preserves_journal_and_never_retries(self):
        self.api.lose.add("create")
        self.api.ambiguous_create = True
        for _ in range(2):
            with self.assertRaisesRegex(release.ReleaseError, "exactly one"):
                self.submit()
        self.assertEqual(len(self.api.writes), 1)
        self.assertIsNotNone(release.read_json(self.f.spec.receipt)["pending"])

    def test_unknown_item_response_recovers_exact_version_relationship(self):
        self.api.lose.add("item")
        result = self.submit()
        self.assertEqual(len(result["item_ids"]), 2)
        self.assertEqual(sum(path == "/v1/reviewSubmissionItems" for _, path, _ in self.api.writes), 2)

    def test_unknown_item_with_wrong_target_is_not_inferred_from_state(self):
        self.api.wrong_item_target = True
        self.api.lose.add("item")
        for _ in range(2):
            with self.assertRaisesRegex(release.ReleaseError, "target relationship"):
                self.submit()
        self.assertEqual(len(self.api.writes), 2)
        self.assertFalse(any(method == "PATCH" for method, _, _ in self.api.writes))

    def test_unknown_submit_accepted_is_read_back_once(self):
        self.api.lose.add("submit")
        self.assertTrue(self.submit()["review_submitted"])
        self.submit()
        self.assertEqual(sum(path.startswith("/v1/reviewSubmissions/") for _, path, _ in self.api.writes), 1)

    def test_eventual_app_waiting_iap_ready_then_waiting_polls_without_resubmit(self):
        self.api.iap_states_after_submit = ["READY_FOR_REVIEW", "WAITING_FOR_REVIEW"]
        with patch.object(release.time, "sleep"):
            result = self.controller().run(True, "submit-autochoice-1.0.23", poll_seconds=1)
        self.assertTrue(result["review_submitted"])
        self.assertEqual([r["iap_version"]["attributes"]["state"] for r in result["poll_readbacks"]],
                         ["READY_FOR_REVIEW", "WAITING_FOR_REVIEW"])
        self.assertTrue(all(r["review_submission"]["attributes"]["state"] == "WAITING_FOR_REVIEW"
                            for r in result["poll_readbacks"]))
        self.assertEqual(len(self.api.writes), 5)

    def test_iap_already_approved_while_app_waiting_is_confirmed_not_public_release(self):
        self.api.iap_states_after_submit = ["APPROVED"]
        result = self.submit()
        self.assertTrue(result["review_submitted"])
        self.assertTrue(result["iap_review_completed"])
        self.assertFalse(result["public_release_claimed"])
        self.assertEqual(len(self.api.writes), 5)

    def test_iap_accepted_while_app_waiting_is_confirmed(self):
        self.api.iap_states_after_submit = ["ACCEPTED"]
        result = self.submit()
        self.assertTrue(result["review_submitted"])
        self.assertFalse(result["iap_review_completed"])
        self.assertEqual(len(self.api.writes), 5)

    def test_prior_approved_iap_with_inactive_owned_container_cannot_start_new_review(self):
        controller = self.owned_prepared_controller()
        controller.journal["submitted_request_at"] = "previous-attempt"
        self.f.iap_version["attributes"]["state"] = "APPROVED"
        with self.assertRaisesRegex(release.ReleaseError, "owned submitted app review"):
            controller.run(True, "submit-autochoice-1.0.23")
        self.assertEqual(self.api.writes, [])

    def test_iap_eventual_timeout_preserves_last_response_and_retry_only_gets(self):
        self.api.iap_states_after_submit = ["READY_FOR_REVIEW"]
        for _ in range(2):
            with self.assertRaisesRegex(release.ReleaseError, "acceptance is not confirmed"):
                self.submit()
        journal = release.read_json(self.f.spec.receipt)
        self.assertEqual(journal["poll_readbacks"][-1]["iap_version"]["attributes"]["state"], "READY_FOR_REVIEW")
        self.assertEqual(journal["poll_readbacks"][-1]["review_submission"]["attributes"]["state"], "WAITING_FOR_REVIEW")
        self.assertIn("acceptance is not confirmed", journal["last_failure"]["message"])
        self.assertFalse(journal["review_submitted"])
        self.assertEqual(len(self.api.writes), 5)
        self.f.iap_version["attributes"]["state"] = "WAITING_FOR_REVIEW"
        self.assertTrue(self.submit()["review_submitted"])
        self.assertEqual(len(self.api.writes), 5)

    def test_iap_rejected_preserves_actual_terminal_response_and_no_repeat_writes(self):
        self.api.iap_states_after_submit = ["REJECTED"]
        with self.assertRaisesRegex(release.ReleaseError, "IAP=REJECTED"):
            self.submit()
        journal = release.read_json(self.f.spec.receipt)
        self.assertEqual(journal["poll_readbacks"][-1]["iap_version"]["attributes"]["state"], "REJECTED")
        self.assertEqual(journal["last_failure"]["type"], "ReleaseError")
        self.assertFalse(journal["review_submitted"])
        with self.assertRaisesRegex(release.ReleaseError, "IAP=REJECTED"):
            self.submit()
        self.assertEqual(len(self.api.writes), 5)

    def test_unknown_unaccepted_submit_never_blind_retries(self):
        self.api.no_apply.add("submit")
        for _ in range(2):
            with self.assertRaisesRegex(release.ReleaseError, "unknown submit"):
                self.submit()
        self.assertEqual(sum(path.startswith("/v1/reviewSubmissions/") for _, path, _ in self.api.writes), 1)
        self.assertFalse(release.read_json(self.f.spec.receipt)["review_submitted"])

    def test_unknown_release_type_is_get_recovered(self):
        self.api.lose.add("release-type")
        self.assertTrue(self.submit()["review_submitted"])
        self.assertEqual(sum(path.startswith("/v1/appStoreVersions/") for _, path, _ in self.api.writes), 1)

    def test_evidence_change_after_freeze_refuses_every_write(self):
        controller = self.controller()
        self.f.edit("proof", lambda p: p.update(source_git_sha="b" * 40))
        with self.assertRaisesRegex(release.ReleaseError, "evidence changed"):
            controller.run(True, "submit-autochoice-1.0.23")
        self.assertEqual(self.api.writes, [])

    def test_source_change_before_next_write_stops_downstream(self):
        def changed_source(e):
            self.source_checks += 1
            if self.source_checks >= 4:
                raise release.ReleaseError("HEAD/origin main/processed source SHA mismatch")
            return {"git_sha": e.sha}
        controller = release.Controller(release.Evidence(self.f.spec), self.api, changed_source)
        with self.assertRaisesRegex(release.ReleaseError, "source SHA"):
            controller.run(True, "submit-autochoice-1.0.23")
        self.assertEqual(len(self.api.writes), 1, "only the earlier verified container creation is allowed")

    def test_incomplete_pagination_is_not_silently_ignored(self):
        self.api.paginated = True
        with self.assertRaisesRegex(release.ReleaseError, "paginated"):
            self.submit()
        self.assertEqual(self.api.writes, [])

    def test_only_whitelisted_review_mutations_are_ever_used(self):
        self.submit()
        for method, path, body in self.api.writes:
            self.assertIn(method, {"POST", "PATCH"})
            self.assertTrue(path in {"/v1/reviewSubmissions", "/v1/reviewSubmissionItems",
                                    "/v1/appStoreVersions/version", "/v1/reviewSubmissions/created"})
            self.assertNotIn("price", json.dumps(body).lower())
            self.assertNotIn("canceled", json.dumps(body).lower())

    def policy_fixture(self, policy, current=None, explicit=True):
        directory = Path(tempfile.mkdtemp(dir=self.root))
        self.f = Fixture(directory)
        self.f.spec = replace(self.f.spec, release_type=policy)
        if explicit:
            self.f.edit("proof", lambda p: p.update(release_type=policy))
        if current is not None:
            self.f.version["attributes"]["releaseType"] = current
        self.api = FakeASC(self.f)

    def legacy_journal(self, pending=None, operations=None):
        evidence = release.Evidence(self.f.spec)
        identity = {k: v for k, v in evidence.identity.items() if k != "release_type"}
        # Independent reproduction of the original json.dumps serialization.
        fingerprint = hashlib.sha256(json.dumps({"identity": identity, "files": evidence.files},
                                                sort_keys=True).encode()).hexdigest()
        journal = {"schema_version": 1, "controller": "autoapp-toolkit/asc_app_review_release.py",
                   "started_at": "historical-start", "identity": identity, "input_sha256": evidence.files,
                   "fingerprint": fingerprint, "operations": operations or [], "pending": pending,
                   "review_submitted": False, "public_release_claimed": False}
        release.save(self.f.spec.receipt, journal)
        return journal

    def legacy_release_operation(self, policy="AFTER_APPROVAL"):
        return {"kind": "release-type", "method": "PATCH", "path": "/v1/appStoreVersions/version",
                "body": {"data": {"type": "appStoreVersions", "id": "version",
                                  "attributes": {"releaseType": policy}}}, "context": {},
                "started_at": "historical-write", "status": "outcome_unknown"}

    def test_manual_already_selected_preserves_reviewed_policy_without_patch(self):
        self.policy_fixture("MANUAL", "MANUAL")
        result = self.submit()
        self.assertEqual(self.f.version["attributes"]["releaseType"], "MANUAL")
        self.assertEqual(result["identity"]["release_type"], "MANUAL")
        self.assertEqual(result["desired_release_type"], "MANUAL")
        self.assertEqual(result["confirmed_release_type"], "MANUAL")
        self.assertTrue(result["release_type_readback"]["matches"])
        self.assertFalse(result["public_release_claimed"])
        self.assertEqual(len(self.api.writes), 4)
        self.assertFalse(any(path.startswith("/v1/appStoreVersions/") for _, path, _ in self.api.writes))
        self.submit()
        self.assertEqual(len(self.api.writes), 4)

    def test_explicit_both_modes_change_opposite_type_once_using_real_payload(self):
        for policy, current in [("MANUAL", "AFTER_APPROVAL"), ("AFTER_APPROVAL", "MANUAL")]:
            with self.subTest(policy=policy):
                self.policy_fixture(policy, current)
                result = self.submit()
                writes = [w for w in self.api.writes if w[1].startswith("/v1/appStoreVersions/")]
                self.assertEqual(len(writes), 1)
                self.assertEqual(writes[0][2]["data"]["attributes"], {"releaseType": policy})
                operation = next(o for o in result["operations"] if o["kind"] == "release-type")
                self.assertEqual(operation["context"], {"release_type": policy, "version_id": "version"})
                self.assertTrue(all(o["context"]["release_type"] == policy for o in result["operations"]))
                self.assertEqual(result["confirmed_release_type"], policy)
                self.submit()
                self.assertEqual(len(self.api.writes), 5)

    def test_read_only_both_modes_distinguish_desired_observed_adjustment(self):
        for policy, current in [("MANUAL", "AFTER_APPROVAL"), ("AFTER_APPROVAL", "MANUAL")]:
            with self.subTest(policy=policy):
                self.policy_fixture(policy, current)
                result = self.controller().run()
                self.assertEqual(result["desired_release_type"], policy)
                readback = result["release_type_readback"]
                self.assertEqual((readback["desired"], readback["observed"], readback["matches"], readback["adjustment_required"]),
                                 (policy, current, False, True))
                self.assertNotIn("confirmed_release_type", result)
                self.assertEqual(self.api.writes, [])

    def test_unsupported_policy_and_manual_missing_or_mismatched_proof_fail_before_api(self):
        for value in ["INVALID", "SCHEDULED", "manual", None, True]:
            with self.subTest(value=value), self.assertRaisesRegex(release.ReleaseError, "release_type"):
                replace(self.f.spec, release_type=value)
        self.policy_fixture("MANUAL", explicit=False)
        with self.assertRaisesRegex(release.ReleaseError, "explicit independent"):
            self.controller()
        for proof in ["AFTER_APPROVAL", "invalid", None, True]:
            self.f.edit("proof", lambda p: p.update(release_type=proof))
            with self.subTest(proof=proof), self.assertRaisesRegex(release.ReleaseError, "release_type mismatch"):
                self.controller()
        self.assertEqual(self.api.gets, [])
        self.assertEqual(self.api.writes, [])

    def test_explicit_after_rejects_manual_proof_and_default_remains_after(self):
        self.assertEqual(self.f.spec.release_type, "AFTER_APPROVAL")
        self.f.edit("proof", lambda p: p.update(release_type="MANUAL"))
        with self.assertRaisesRegex(release.ReleaseError, "release_type mismatch"):
            self.controller()
        self.assertEqual(self.api.gets, [])

    def test_policy_is_part_of_fingerprint_even_with_same_input_hashes(self):
        evidence = release.Evidence(self.f.spec)
        same_files_other_policy = dict(evidence.identity, release_type="MANUAL")
        other = hashlib.sha256(json.dumps({"identity": same_files_other_policy, "files": evidence.files},
                                         sort_keys=True).encode()).hexdigest()
        self.assertNotEqual(evidence.fingerprint, other)
        self.assertNotEqual(evidence.fingerprint, evidence.legacy_fingerprint)
        self.assertEqual(evidence.identity["release_type"], "AFTER_APPROVAL")

    def test_changed_policy_and_evidence_cannot_recover_or_mutate_existing_journal(self):
        self.policy_fixture("AFTER_APPROVAL", "MANUAL")
        self.api.crash.add("release-type")
        with self.assertRaises(SystemExit):
            self.submit()
        before = self.f.spec.receipt.read_bytes()
        get_count, write_count = len(self.api.gets), len(self.api.writes)
        self.f.spec = replace(self.f.spec, release_type="MANUAL")
        self.f.edit("proof", lambda p: p.update(release_type="MANUAL"))
        with self.assertRaisesRegex(release.ReleaseError, "different release evidence or policy"):
            self.controller()
        self.assertEqual(self.f.spec.receipt.read_bytes(), before)
        self.assertEqual((len(self.api.gets), len(self.api.writes)), (get_count, write_count))

    def test_exact_legacy_after_journal_keeps_original_identity_hashes_and_history(self):
        self.policy_fixture("AFTER_APPROVAL", explicit=False)
        original = self.legacy_journal()
        result = self.controller().run()
        for key in ["identity", "input_sha256", "fingerprint", "started_at", "operations"]:
            self.assertEqual(result[key], original[key])
        self.assertEqual(result["legacy_release_type_interpretation"]["release_type"], "AFTER_APPROVAL")
        self.assertEqual(result["desired_release_type"], "AFTER_APPROVAL")
        self.assertEqual(self.api.writes, [])
        self.assertTrue(self.submit()["review_submitted"])
        self.submit()
        self.assertEqual(len(self.api.writes), 5)

    def test_legacy_manual_rejected_even_if_proof_and_old_hash_are_manually_consistent(self):
        self.policy_fixture("MANUAL", "MANUAL")
        self.legacy_journal()
        before = self.f.spec.receipt.read_bytes()
        with self.assertRaisesRegex(release.ReleaseError, "legacy journal cannot be reused for MANUAL"):
            self.controller()
        self.assertEqual(self.f.spec.receipt.read_bytes(), before)
        self.assertEqual(self.api.gets, [])
        self.assertEqual(self.api.writes, [])

    def test_legacy_exact_original_serialization_and_all_identity_hashes_required(self):
        for alteration in ["compact", "identity", "input_hash", "fingerprint"]:
            with self.subTest(alteration=alteration):
                self.policy_fixture("AFTER_APPROVAL", explicit=False)
                journal = self.legacy_journal()
                if alteration == "compact":
                    journal["fingerprint"] = hashlib.sha256(json.dumps({"identity": journal["identity"], "files": journal["input_sha256"]},
                                                                      sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                elif alteration == "identity":
                    journal["identity"]["build"] = "99"
                elif alteration == "input_hash":
                    journal["input_sha256"][str(self.f.paths["proof"].resolve())] = "b" * 64
                else:
                    journal["fingerprint"] = "b" * 64
                release.save(self.f.spec.receipt, journal)
                with self.assertRaisesRegex(release.ReleaseError, "exact original"):
                    self.controller()
                self.assertEqual(self.api.gets, [])

    def test_legacy_contradictory_release_operations_or_malformed_records_fail_closed(self):
        changes = [lambda j: j.update(desired_release_type="MANUAL"),
                   lambda j: j.update(legacy_release_type_interpretation={"release_type": "MANUAL"}),
                   lambda j: j.update(operations={}), lambda j: j.update(pending={}),
                   lambda j: j["operations"].append(None),
                   lambda j: j["operations"][0]["body"]["data"]["attributes"].update(releaseType="MANUAL"),
                   lambda j: j["operations"][0]["body"]["data"].update(id="foreign-version"),
                   lambda j: j["operations"][0].update(path="/v1/appStoreVersions/foreign"),
                   lambda j: j["operations"][0].update(method="POST"),
                   lambda j: j["operations"][0].update(context={"release_type": "MANUAL"}),
                   lambda j: j["operations"][0]["body"]["data"]["attributes"].update(price="changed")]
        for change in changes:
            self.policy_fixture("AFTER_APPROVAL", explicit=False)
            journal = self.legacy_journal(operations=[self.legacy_release_operation()])
            change(journal)
            release.save(self.f.spec.receipt, journal)
            before = self.f.spec.receipt.read_bytes()
            with self.assertRaises(release.ReleaseError):
                self.controller()
            self.assertEqual(self.f.spec.receipt.read_bytes(), before)
            self.assertEqual(self.api.gets, [])
            self.assertEqual(self.api.writes, [])

    def test_legacy_release_pending_recovers_exact_recorded_version_without_patch(self):
        self.policy_fixture("AFTER_APPROVAL", "AFTER_APPROVAL", explicit=False)
        journal = self.legacy_journal(pending=self.legacy_release_operation())
        journal["review_submission_id"] = "owned"
        release.save(self.f.spec.receipt, journal)
        self.api.add_container("owned")
        controller = self.controller()
        controller.resolve_pending()
        self.assertEqual(self.api.gets, [("/v1/appStoreVersions/version", None)])
        self.assertEqual(self.api.writes, [])
        self.assertIsNone(controller.journal["pending"])
        self.assertEqual(controller.journal["operations"][0]["context"], {}, "do not rewrite historical operation")

    def test_both_modes_release_response_loss_and_crash_recover_without_duplicate_patch(self):
        for policy, current in [("MANUAL", "AFTER_APPROVAL"), ("AFTER_APPROVAL", "MANUAL")]:
            for failure in ["lose", "crash"]:
                with self.subTest(policy=policy, failure=failure):
                    self.policy_fixture(policy, current)
                    getattr(self.api, failure).add("release-type")
                    if failure == "crash":
                        with self.assertRaises(SystemExit):
                            self.submit()
                        pending = release.read_json(self.f.spec.receipt)["pending"]
                        self.assertEqual(pending["body"]["data"]["attributes"], {"releaseType": policy})
                        self.api.crash.clear()
                    result = self.submit()
                    self.assertEqual(result["confirmed_release_type"], policy)
                    self.submit()
                    self.assertEqual(sum(p.startswith("/v1/appStoreVersions/") for _, p, _ in self.api.writes), 1)

    def test_both_modes_unapplied_release_patch_retains_pending_and_never_replays(self):
        for policy, current in [("MANUAL", "AFTER_APPROVAL"), ("AFTER_APPROVAL", "MANUAL")]:
            with self.subTest(policy=policy):
                self.policy_fixture(policy, current)
                self.api.no_apply.add("release-type")
                for _ in range(2):
                    with self.assertRaisesRegex(release.ReleaseError, "unknown release-type outcome"):
                        self.submit()
                journal = release.read_json(self.f.spec.receipt)
                self.assertEqual(journal["pending"]["kind"], "release-type")
                self.assertEqual(journal["pending"]["release_type_recovery_readback"]["observed"], current)
                self.assertFalse(journal["pending"]["release_type_recovery_readback"]["matches"])
                self.assertEqual(sum(p.startswith("/v1/appStoreVersions/") for _, p, _ in self.api.writes), 1)
                self.assertFalse(any(p.startswith("/v1/reviewSubmissions/") for _, p, _ in self.api.writes))

    def test_unknown_release_type_get_preserves_pending_without_retransmission(self):
        for policy, current in [("MANUAL", "AFTER_APPROVAL"), ("AFTER_APPROVAL", "MANUAL")]:
            self.policy_fixture(policy, current)
            self.api.no_apply.add("release-type")
            with self.assertRaisesRegex(release.ReleaseError, "unknown release-type outcome"):
                self.submit()
            writes = len(self.api.writes)
            self.f.version["attributes"].pop("releaseType")
            with self.assertRaisesRegex(release.ReleaseError, "unknown release-type outcome"):
                self.controller().run()
            self.assertEqual(len(self.api.writes), writes)
            pending = release.read_json(self.f.spec.receipt)["pending"]
            self.assertEqual(pending["kind"], "release-type")
            self.assertIsNone(pending["release_type_recovery_readback"]["observed"])

    def test_new_journal_operation_policy_and_release_payload_are_validated_before_get(self):
        self.policy_fixture("MANUAL", "AFTER_APPROVAL")
        self.api.crash.add("release-type")
        with self.assertRaises(SystemExit):
            self.submit()
        original = release.read_json(self.f.spec.receipt)
        before_counts = len(self.api.gets), len(self.api.writes)
        for key, value in [("release_type", "AFTER_APPROVAL"), ("version_id", "foreign")]:
            journal = copy.deepcopy(original)
            journal["pending"]["context"][key] = value
            release.save(self.f.spec.receipt, journal)
            with self.assertRaises(release.ReleaseError):
                self.controller()
            self.assertEqual((len(self.api.gets), len(self.api.writes)), before_counts)

    def test_contradictory_direct_type_write_is_blocked_before_source_or_api(self):
        self.policy_fixture("MANUAL", "MANUAL")
        controller = self.controller()
        with self.assertRaisesRegex(release.ReleaseError, "target/body mismatch"):
            controller.mutate("release-type", "PATCH", "/v1/appStoreVersions/version", {"data": {
                "type": "appStoreVersions", "id": "version", "attributes": {"releaseType": "AFTER_APPROVAL"}}}, {})
        self.assertEqual(self.api.gets, [])
        self.assertEqual(self.api.writes, [])

    def test_wrong_type_late_before_submit_blocks_both_modes(self):
        for policy, opposite in [("MANUAL", "AFTER_APPROVAL"), ("AFTER_APPROVAL", "MANUAL")]:
            with self.subTest(policy=policy):
                self.policy_fixture(policy, policy)
                controller = self.owned_prepared_controller()
                self.f.version["attributes"]["releaseType"] = policy
                get = self.api.get
                def late_get(path, params=None):
                    payload = get(path, params)
                    if path == "/v1/appInfoLocalizations/info-loc-de-DE":
                        self.f.version["attributes"]["releaseType"] = opposite
                    return payload
                self.api.get = late_get
                with self.assertRaisesRegex(release.ReleaseError, "selected release type changed before submission"):
                    controller.mutate("submit", "PATCH", "/v1/reviewSubmissions/owned", {"data": {
                        "type": "reviewSubmissions", "id": "owned", "attributes": {"submitted": True}}}, {})
                self.assertEqual(self.api.writes, [])

    def test_wrong_type_during_confirmation_and_read_only_resume_never_switches_policy(self):
        for policy, opposite in [("MANUAL", "AFTER_APPROVAL"), ("AFTER_APPROVAL", "MANUAL")]:
            with self.subTest(policy=policy):
                self.policy_fixture(policy, policy)
                write = self.api.write
                def changed_after_submit(method, path, body):
                    result = write(method, path, body)
                    if path.startswith("/v1/reviewSubmissions/"):
                        self.f.version["attributes"]["releaseType"] = opposite
                    return result
                self.api.write = changed_after_submit
                with self.assertRaisesRegex(release.ReleaseError, "release type changed after submission"):
                    self.submit()
                writes = len(self.api.writes)
                journal = release.read_json(self.f.spec.receipt)
                self.assertEqual(journal["release_type_readback"]["observed"], opposite)
                self.assertFalse(journal["review_submitted"])
                with self.assertRaisesRegex(release.ReleaseError, "release type changed after submission"):
                    self.controller().run()
                self.assertEqual(len(self.api.writes), writes)

    def test_manual_submit_response_loss_and_process_death_remain_idempotent(self):
        for failure in ["lose", "crash"]:
            self.policy_fixture("MANUAL", "MANUAL")
            getattr(self.api, failure).add("submit")
            if failure == "crash":
                with self.assertRaises(SystemExit):
                    self.submit()
                self.api.crash.clear()
            self.assertTrue(self.submit()["review_submitted"])
            self.submit()
            self.assertEqual(sum(p.startswith("/v1/reviewSubmissions/") for _, p, _ in self.api.writes), 1)

    def test_missing_or_unsupported_live_type_blocks_before_any_write(self):
        for current in [None, "SCHEDULED", "unknown"]:
            self.policy_fixture("MANUAL", "MANUAL")
            self.f.version["attributes"]["releaseType"] = current
            with self.assertRaisesRegex(release.ReleaseError, "live release type is missing or unsupported"):
                self.submit()
            self.assertEqual(self.api.writes, [])

    def test_cli_accepts_both_modes_default_and_rejects_invalid_before_evidence(self):
        required = []
        for name in ["repo", "release-receipt", "metadata-receipt", "iap-metadata-receipt", "iap-image-receipt",
                     "paywall-image", "store-image-receipt", "stage-receipt", "independent-preflight", "receipt"]:
            required.extend(["--" + name, "fixture-path"])
        for name in ["app-id", "iap-id", "product-id", "version", "build"]:
            required.extend(["--" + name, "fixture-value"])
        observed = []
        def stop_at_evidence(spec):
            observed.append(spec.release_type)
            raise release.ReleaseError("offline stop")
        for flags in [[], ["--release-type", "MANUAL"], ["--release-type", "AFTER_APPROVAL"]]:
            with patch.object(release, "Evidence", side_effect=stop_at_evidence), patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(release.main(required + flags), 1)
        self.assertEqual(observed, ["AFTER_APPROVAL", "MANUAL", "AFTER_APPROVAL"])
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as caught:
            release.main(required + ["--release-type", "SCHEDULED"])
        self.assertEqual(caught.exception.code, 2)


class RealGitContractTests(unittest.TestCase):
    def test_clean_remote_main_exact_source_then_dirty_or_foreign_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            repo = root / "repo"
            def git(*args, cwd=root):
                return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
            git("init", "--bare", str(remote))
            git("init", "-b", "main", str(repo))
            git("config", "user.name", "Offline Test", cwd=repo)
            git("config", "user.email", "offline@example.invalid", cwd=repo)
            (repo / "source").write_text("native input\n")
            git("add", "source", cwd=repo)
            git("commit", "-m", "fixture", cwd=repo)
            git("remote", "add", "origin", str(remote), cwd=repo)
            git("push", "-u", "origin", "main", cwd=repo)
            fixture = Fixture(root)
            fixture.spec = replace(fixture.spec, repo=repo)
            for key in ["release", "metadata", "stage", "proof", "store-image"]:
                def update(p):
                    sha = git("rev-parse", "HEAD", cwd=repo)
                    if key == "release":
                        p["source"].update(git_sha=sha, remote_sha=sha, tracking_sha=sha)
                    else:
                        p["source_git_sha"] = sha
                fixture.edit(key, update)
            plan = release.read_json(root / "store-plan.json")
            plan["source_git_sha"] = git("rev-parse", "HEAD", cwd=repo)
            (root / "store-plan.json").write_text(json.dumps(plan))
            evidence = release.Evidence(fixture.spec)
            self.assertEqual(release.git_contract(evidence)["remote_sha"], evidence.sha)
            evidence.sha = "b" * 40
            with self.assertRaisesRegex(release.ReleaseError, "source SHA"):
                release.git_contract(evidence)
            (repo / "source").write_text("uncommitted change\n")
            with self.assertRaisesRegex(release.ReleaseError, "dirty"):
                release.git_contract(evidence)


if __name__ == "__main__":
    unittest.main()
