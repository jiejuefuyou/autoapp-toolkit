# Exact app and IAP metadata review release

`scripts/asc_app_review_release.py` submits one already processed, independently
reviewed iOS candidate together with a new metadata version of its existing
approved, nonconsumable IAP. Its default invocation performs authenticated GETs
and writes a local preflight journal. Add `--submit` and the exact confirmation
only after the app's release gate is complete.

This is the authenticated local `main` path. It does not require a workflow
dispatch, an Apple ID session, a new toolkit installation in GitHub Actions, or
a new binary upload. It reuses `asc_monitor.load_credentials` and
`asc_monitor.mint_token`, with the existing private key/config or environment.
Never put credentials, device backups, review contact details, or release
receipts into Git. The JSON receipt and lock are private local files.

## Required inputs

All inputs must refer to the same version, positive integer build, ASC build ID
and 40-character source commit. The controller checks clean local `main`,
freshly fetched `origin/main`, remote `refs/heads/main` and the processed source
SHA before writes. No latest-build selection is performed.

| CLI input | Required evidence |
| --- | --- |
| `--repo` | Clean candidate repository on `main`, with its configured `origin` |
| `--app-id --iap-id --product-id --version --build` | Exact app, existing approved SKU, version and build identities |
| `--release-receipt` | `local_testflight_release.py` processed release receipt: signed IPA at `artifact.ipa_path`, exact `ipa_sha256`, processed build and clean remote source readback |
| `--metadata-receipt` | Applied draft metadata receipt: exact source, version ID, build ID and `readbacks` containing version/app-info localization IDs and reviewed field values |
| `--iap-metadata-receipt` | Version-based IAP metadata applied receipt: unchanged parent SKU, no price writes, new version ID, version resource and localization readbacks |
| `--iap-image-receipt --paywall-image` | Current parent review screenshot receipt and actual PNG: matching SHA-256/MD5, asset ID and `COMPLETE`; `capture_and_visual_receipt` must record exact SHA-256 and completed visual review |
| `--store-image-receipt` | Current screenshot upload receipt: `screenshots_complete`, source SHA, `requires_ipad`, `selected_plan`, and `uploaded` asset mappings |
| `--stage-receipt` | Successful exact App Store stage with all checks passing |
| `--independent-preflight` | Independent read-only review, `ready_for_submission: true`, no blockers, exact IDs/SHA, and all checks passing |
| `--receipt` | One stable journal path for this candidate and this immutable set of inputs |

The store screenshot receipt's `uploaded` entries contain `locale`,
`display_type`, `id`, `set_id`, `source_file_checksum`, `source_sha256` and
`dimensions`. Its selection plan contains `visual_review_completed: true`, the
same source SHA/`requires_ipad`, and `images` entries with `locale`,
`display_type`, `path`, `sha256` and `dimensions`. Relative referenced paths are
resolved beside the referencing JSON. Preserve the original PNGs and receipts.
The controller freezes them, checks PNG header dimensions, then reads the exact
selected App Store version's localizations, screenshot sets and assets. Every
live ID, set, checksum, dimension and delivery state must equal the reviewed
selection; foreign, missing or still-processing screenshots block all writes.
Metadata must cover all eight release locales. Screenshots require a current
primary English phone image; other locales may use the primary-language
fallback when that is the independently reviewed release selection. The receipt
honestly records `store_screenshot_locales` and does not claim absent localized
screenshots. The processed IPA's exact hash and `Info.plist` prove bundle,
version, build and actual `UIDeviceFamily`; `requires_ipad` must equal its tablet
support. Tablet apps need a current primary English native tablet image. Native
tablet layout and visual usability are independent review duties, not something
a dimension check can prove.

The independent preflight uses `checks` entries with `pass: true` (or `ok:
true`), `independent_reviewer_no_mutations: true`, `remaining_blockers: []`, and
the exact `app_id`, `bundle_id`, `version`, `build`, `build_id`,
`source_git_sha`, `app_store_version_id` and `new_iap_metadata_version_id`.
Do not fabricate these fields to satisfy the controller.

For **PromptVault and WaterNow**, these additional independent proof fields
must be JSON booleans `true`:

- `physical_release_gate_completed`
- `exact_processed_build_installed_on_physical_device`

PromptVault also requires `two_same_account_real_devices_including_ipad: true`.
A simulator, server TestFlight eligibility, direct-install smoke test or one
physical phone cannot substitute for these app-specific release requirements.
The apps' release-gate documents and issue/evidence prerequisites remain in
force. This tool does not waive their camera, notifications, cloud/conflict,
account, installation, transaction, tag or issue gates. An independent reviewer
must record their actual completion before declaring the candidate ready.
Account Holder agreements, tax/banking and legal forms remain separately
reviewed business evidence; the controller never accepts them.

## Invocation

Use the candidate's actual identities and private evidence paths. For example,
keep the full arguments in a Bash array so the read-only and submit invocation
use exactly the same evidence:

```bash
toolkit_dir=/path/to/autoapp-toolkit
candidate_repo=/path/to/app-repository
evidence_dir=/path/to/private-release-evidence
args=(
  --repo "$candidate_repo"
  --app-id "$ASC_APP_ID" --iap-id "$ASC_IAP_ID"
  --product-id "$EXISTING_PRODUCT_ID"
  --version "$RELEASE_VERSION" --build "$RELEASE_BUILD_NUMBER"
  --release-receipt "$evidence_dir/processed-release.json"
  --metadata-receipt "$evidence_dir/candidate-metadata-applied.json"
  --iap-metadata-receipt "$evidence_dir/iap-candidate-metadata-applied.json"
  --iap-image-receipt "$evidence_dir/iap-review-image-applied.json"
  --paywall-image "$evidence_dir/current-native-paywall.png"
  --store-image-receipt "$evidence_dir/screenshots-applied.json"
  --stage-receipt "$evidence_dir/stage-gate.json"
  --independent-preflight "$evidence_dir/independent-submit-preflight.json"
  --receipt "$evidence_dir/app-review-release-journal.json"
)
python3 "$toolkit_dir/scripts/asc_app_review_release.py" "${args[@]}"
# Only when every required gate is actually complete:
python3 "$toolkit_dir/scripts/asc_app_review_release.py" "${args[@]}" \
  --submit --confirm "submit-promptvault-1.2.3"
```

WaterNow's confirmation is `submit-waternow-1.0.7`; use the app name and version
from the exact processed receipt for other candidates. The confirmation alone
does not complete any gate. Run this controller from an authenticated Mac or
Linux host; the local lock uses `fcntl`, and no Windows behavior is claimed.

## Mutation and recovery contract

Only these ASC mutations are possible:

1. Reuse a `READY_FOR_REVIEW` iOS container only when `submittedDate` is explicitly
   null, its item list is empty and its item paging total is zero; otherwise
   create one new submission for the exact app. Closed `COMPLETE` history is
   left untouched. Other nonempty, submitted or unresolved containers block
   writes. Never adopt, cancel, delete or rewrite somebody else's container.
2. Add exactly one `appStoreVersion` item and one `inAppPurchaseVersion` item.
   Read their actual target relationships through the submission's scoped
   `/items?include=appStoreVersion,inAppPurchaseVersion` endpoint.
3. Set only the candidate version's `releaseType` to `AFTER_APPROVAL`.
4. Mark only the journal-owned exact two-item submission as submitted. Immediately
   before transmission, check the selected VALID build, clean exact source,
   never-submitted READY container, exact item targets and release type again.

Every write is durably journaled before transmission. A timeout, lost response
or process interruption is resolved by scoped GETs. A unique new resource must
match the journaled app/target; ambiguous or unconfirmed results retain a
pending entry and stop. Re-running the same arguments/journal reads the original
pending operation instead of resending it. Never delete the journal and retry
an uncertain create/submit. A changed evidence fingerprint blocks reuse; obtain
an independent classification of the old operation before preparing a new
candidate or journal.

App and submission must reach `WAITING_FOR_REVIEW` or `IN_REVIEW`; the owned IAP
metadata version must be waiting/in review, `ACCEPTED`, or already `APPROVED`.
App/submission readiness can precede the IAP's state transition: a still
`PREPARE_FOR_SUBMISSION`/`READY_FOR_REVIEW` IAP is polled for up to 90 seconds,
without another POST/PATCH. Each poll's actual app, submission, IAP, build and
item responses are saved privately; a timeout or rejected/unresolved state
preserves the last response and an explicit failure. Resume uses GETs, not a
second submission. An already approved IAP is recorded as `iap_review_completed`,
without claiming that the app is approved or public.
The selected build/release type/two targets must still match. Only then is
`review_submitted: true` written. `public_release_claimed` stays false: acceptance
into review does not prove approval or public availability. Continue the app's
required status monitoring (including its 30-minute readback where required)
with the read-only ASC monitor and retain submission/item IDs in the private
release record. No external issue comment is emitted by this tool.

## API provenance and legacy path limits

Apple's [migration guide](https://developer.apple.com/documentation/appstoreconnectapi/migrating-in-app-purchase-metadata-to-v2)
states that version-based IAP metadata and review items were introduced in API
4.4.1. It also states that the App Review screenshot still attaches to the
parent IAP. The endpoint/request shapes in this implementation were checked
against the current official OpenAPI **4.5** schema, not a cached 4.4.1 schema.
The preceding task-specific implementation submitted five actual candidates
with app + new IAP version items and `AFTER_APPROVAL`. This durable controller
adds stricter source, screenshot, target and recovery guards. Its offline
contract tests are not a live submission receipt. The scoped `/items` GET and
its exact app/IAP target relationships were additionally confirmed read-only
against a current production submission; this is a component check, not a live
end-to-end execution of the new controller.

Legacy Fastlane release lanes are a separate route. The reviewed installed
Fastlane 2.236.1 `deliver` submitter adds the app version only and refuses an
externally populated review container; parent IAP `APPROVED` does not prove that
new version metadata is included in the same review. The app lockfiles pin
2.237.0, so this observation is not a claim about an uninspected exact installed
2.237.0 implementation. Those lanes do not provide this controller's verified
two-item contract. `automatic_release: false` also requests `MANUAL`, while
this release requires `AFTER_APPROVAL`. The app owners corrected their release
settings and document the independent local route; do not let a legacy lane
overwrite an already prepared candidate.

Do not dispatch a beta build or push a matching TestFlight tag just to satisfy
a release prerequisite after an exact local upload: the historical workflows
can rebuild with a workflow-run build number. Follow the app's current gate and
approved local `main` route instead. This controller neither dispatches a
workflow nor alters its installation logic, binaries, testers, accounts, IAP
products, SKU, price or screenshot assets.

## Offline validation

```bash
python3 -m py_compile scripts/asc_app_review_release.py
python3 -m unittest discover -s tests -p 'test_asc_app_review_release.py' -v
python3 scripts/asc_app_review_release.py --help
```

Tests exercise foreign/unprocessed builds, exact remote Git source, missing
physical/tablet gates, stale metadata/images, foreign review items, late
concurrent changes, empty-container reuse, lost responses, process interruption,
ambiguous creation and replay without another mutation. They use a fake ASC
server and temporary local Git repositories; they do not call ASC or devices.
