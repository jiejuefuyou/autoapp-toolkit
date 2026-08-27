# TestFlight and physical-device distribution runbook

This runbook exists because an uploaded build, an internal beta group, an
invited email, an accepted invitation, an installed device build, and an App
Store review submission are different states. Never collapse them into “it is
on TestFlight” or “it shipped.”

## Default route

Use Xcode direct install for local physical-device validation. Use TestFlight
only when the thing being tested is TestFlight distribution itself.

After every physical UI-test run, remove the generated XCTest runner and prove
that exactly one canonical app remains:

```bash
python3 autoapp-toolkit/scripts/device_app_hygiene.py \
  --device "$PHYSICAL_TEST_DEVICE" \
  --bundle com.example.app \
  --version 1.0.0 \
  --build 1 \
  --distribution direct \
  --clean-test-runners \
  --receipt .verify/device-app-hygiene.json
```

The cleanup selector is deliberately narrow: it can remove only a
developer-signed XCTest runner whose bundle identifier begins with the exact
canonical product bundle identifier. It never removes another product.

For a local App Store archive and TestFlight upload, use the shared exact-build
gate. Omit `--upload` first to prove the archive, IPA identity, signing profile,
source SHA, and remote readback without changing App Store Connect:

```bash
python3 autoapp-toolkit/scripts/local_testflight_release.py \
  --repo autoapp/repos/example \
  --project Example.xcodeproj \
  --scheme Example \
  --app-name Example \
  --bundle com.example.app \
  --version 1.0.0 \
  --build 1 \
  --git-sha "$(git -C autoapp/repos/example rev-parse HEAD)"
```

Only add `--upload` after the artifact receipt has been inspected. The tool
refuses dirty, divergent, non-`main`, or remote-unreadable source; verifies the
IPA bundle/version/build, code signature, encryption declaration, and App Store
profile; then waits for the exact ASC train/build to become valid with a real
icon. It does not distribute the build to testers, claim a physical install,
or submit anything to App Review.

## Before any internal TestFlight attempt

Run the read-only server gate:

```bash
python3 autoapp-toolkit/scripts/asc_testflight_readiness.py \
  --bundle com.example.app \
  --train 1.0.0 \
  --build 1 \
  --group "Internal Testers" \
  --tester tester@example.com \
  --receipt .verify/asc-testflight-server.json
```

The command fails unless it can prove all of these:

- the exact build is processed, unexpired, has a real icon, and has resolved
  export-compliance state;
- internal testing is enabled for the build;
- the exact internal group grants the exact build;
- the exact tester belongs to the group and has accepted the invitation;
- the tester is also a real App Store Connect user with an eligible role and
  access to the app.

Creating a `betaTester` resource is not proof of internal-tester eligibility.
Internal testers must be App Store Connect users; arbitrary email-only testers
belong in the external-testing flow.

`ASC_TESTFLIGHT_SERVER_GATE_OK` is intentionally server-only. Apple does not
expose which Apple Account redeemed the invitation on the target device, the
current TestFlight session on that device, or an exact-build installation
receipt through the App Store Connect API. A successful device install must be
proved separately.

## ASC settings that still require deliberate review

Before paid distribution or an app with In-App Purchases, the Account Holder
must inspect **Business** in App Store Connect:

- the Paid Apps Agreement is active and no revised agreement is waiting;
- required tax forms are complete;
- banking information is active and not pending correction;
- any new Apple Developer Program agreement has been accepted by the Account
  Holder.

Those states are not inferred from a successful binary upload, and legal
agreements must not be accepted automatically by an agent.

For each app, also verify:

- Users and Access: the internal tester accepted the ASC user invitation, has
  Account Holder/Admin/App Manager/Developer/Marketing role, and can see the
  app;
- TestFlight: the build is in the intended group, the tester is in that same
  group, the invitation state is Accepted or Installed, and automatic
  distribution is either deliberately enabled or every new build is added
  explicitly;
- build: correct platform, minimum OS, device family, icon, signing identifiers,
  export-compliance answer, processing state, and 90-day expiry;
- IAP: product metadata, localizations, price, availability, review screenshot,
  and first-IAP submission together with the app version;
- App Review: the exact app version and each required IAP appear in the same
  review submission, followed by a live state readback.

## Stop rule

One failed TestFlight install is a diagnostic event. Do not recreate groups,
reinvite testers, switch accounts repeatedly, or install more builds until the
failed layer has been identified. If physical product validation is the goal,
use direct install and keep TestFlight troubleshooting as a separate task.

## Incident evidence: AltitudeNow, 2026-08-15

For AltitudeNow 1.0.6 build 20, live ASC evidence showed a valid, unexpired,
App-Store-eligible build with a real icon, resolved encryption declaration,
and `IN_BETA_TESTING`. The `AltitudeNow Release Recovery` group contained build
20 and both ASC users; the Hotmail tester was `ACCEPTED`, while the QQ tester
remained `INVITED`. The default `Internal Testers` group contained neither build
20 nor any tester.

That evidence rules out a malformed binary and proves that earlier automation
was too willing to call tester creation “complete.” It does not prove the
target device's TestFlight account binding, so the persistent “requested app is
unavailable” message is classified at that unobservable device/session layer,
not asserted to be an Apple platform bug.

The separate no-icon problem was reproduced and resolved conclusively: Xcode's
physical UI test left
`com.jiejuefuyou.altitudenow.physical-uitests.xctrunner` installed beside the
real app. Removing that runner left exactly one app,
`com.jiejuefuyou.altitudenow` version 1.0.6 build 20, with a device-generated
non-placeholder icon.

## Apple references

- [Add internal testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers/)
- [Add testers to builds](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-testers-to-builds)
- [TestFlight tester information](https://developer.apple.com/help/app-store-connect/reference/testflight/testflight-tester-information)
- [TestFlight overview](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview)
- [Export compliance overview](https://developer.apple.com/help/app-store-connect/manage-app-information/overview-of-export-compliance)
- [Sign and update agreements](https://developer.apple.com/help/app-store-connect/manage-agreements/sign-and-update-agreements)
- [View agreement statuses](https://developer.apple.com/help/app-store-connect/manage-agreements/view-agreements-status)
- [Provide tax information](https://developer.apple.com/help/app-store-connect/manage-tax-information/provide-tax-information)
- [Enter banking information](https://developer.apple.com/help/app-store-connect/manage-banking-information/enter-banking-information)
