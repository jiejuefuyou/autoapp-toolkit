# AutoApp distribution contract

## Physical-device testing

- Default to Xcode direct install for product and sensor validation.
- TestFlight is a separate distribution test, never a prerequisite for local
  physical validation.
- After every physical UI test, run `scripts/device_app_hygiene.py` with the
  exact bundle, version, build, device, and `--clean-test-runners`.
- A run is incomplete while an `*.xctrunner` app remains on the device or a real
  non-placeholder product icon cannot be fetched.
- Never delete apps outside the exact requested bundle family.

## TestFlight

- Before an internal install attempt, run
  `scripts/asc_testflight_readiness.py` for the exact build, group, and tester.
- Creating a `betaTester` is not equivalent to creating or validating an
  internal tester. Prove the same email is an accepted App Store Connect user
  with an eligible role and app access.
- Treat app, build, group, tester, ASC user, device session, installed app, IAP,
  agreements, and review submission as independent states.
- `ASC_TESTFLIGHT_SERVER_GATE_OK` proves only server configuration. Do not call
  it an installation receipt.
- After one unexplained install failure, stop mutation and identify the failed
  layer. Do not loop through group recreation, reinvites, account switching, or
  repeated installs.

## Agreements and release claims

- Paid Apps Agreement, tax, banking, and revised legal terms require a live
  Account Holder review. Never infer them from upload success.
- Never accept a legal agreement automatically.
- “Uploaded,” “available to an internal group,” “installed on a device,” and
  “submitted for App Review” are different claims and require separate evidence.
