"""Execute byte-identical verify.sh with exported, synthetic Bash tool functions.

This covers shell ordering and failure propagation only. No native build,
simulator, audio device, or Maestro session is started by these tests.
Set QUIET_CHAIN_EVIDENCE_DIR to retain fixture ledgers for an audit; ordinary
test runs clean their temporary files.
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = "QuietFixture"
EXISTING_UDID = "00000000-0000-4000-8000-000000000099"

# Every original external development tool resolves to one of these exported
# functions, even after verify.sh overwrites PATH. Inline Python remains real.
LAUNCHER = r'''#!/bin/bash
set -uo pipefail
xcrun() { "$QUIET_CHAIN_REAL_PYTHON" "$QUIET_CHAIN_STUB" xcrun "$@"; }
xcodegen() { "$QUIET_CHAIN_REAL_PYTHON" "$QUIET_CHAIN_STUB" xcodegen "$@"; }
xcodebuild() { "$QUIET_CHAIN_REAL_PYTHON" "$QUIET_CHAIN_STUB" xcodebuild "$@"; }
maestro() { "$QUIET_CHAIN_REAL_PYTHON" "$QUIET_CHAIN_STUB" maestro "$@"; }
python3() {
  case "${1-}" in
    */require_quiet_simulator.py)
      "$QUIET_CHAIN_REAL_PYTHON" "$QUIET_CHAIN_STUB" quiet "${@:2}" ;;
    */judge.py)
      "$QUIET_CHAIN_REAL_PYTHON" "$QUIET_CHAIN_STUB" judge "${@:2}" ;;
    -c|-) "$QUIET_CHAIN_REAL_PYTHON" "$@" ;;
    *) printf '%s\n' "unexpected Python operation: ${1-}" >&2; return 70 ;;
  esac
}
export -f xcrun xcodegen xcodebuild maestro python3 || exit $?
/bin/bash -c 'declare -F xcrun xcodegen xcodebuild maestro python3 >/dev/null' || exit $?
exec /bin/bash "$QUIET_CHAIN_VERIFY" QuietFixture "$@"
'''


STUB = r'''from __future__ import annotations
import json
import os
import plistlib
import sys
from pathlib import Path

tool, *args = sys.argv[1:]
base = Path(os.environ["QUIET_CHAIN_CASE"])
ledger = base / "ledger.jsonl"
def count(name):
    target = base / (name + ".counter")
    number = int(target.read_text()) + 1 if target.exists() else 1
    target.write_text(str(number))
    return number

def emit(status=0, **extra):
    with ledger.open("a") as out:
        out.write(json.dumps({"tool": tool, "args": args, "status": status, **extra}) + "\n")

def reject(message):
    emit(70, unexpected=message)
    print(message, file=sys.stderr)
    raise SystemExit(70)

if tool == "quiet":
    invocation = count("quiet")
    if len(args) != 1:
        reject("quiet must receive exactly one UUID")
    rc = 2 if invocation == int(os.environ.get("QUIET_CHAIN_FAIL_QUIET", "0")) else 0
    emit(rc, invocation=invocation)
    if rc:
        print("TEST_AUDIO_NOT_QUIET: synthetic injected failure", file=sys.stderr)
    else:
        print(json.dumps({"udid": args[0], "volume": 0, "state": "quiet", "synthetic": True}))
    raise SystemExit(rc)

if tool == "xcrun":
    if args[:3] == ["simctl", "list", "devicetypes"]:
        emit()
        print(json.dumps({"devicetypes": [{"name": "iPhone 16 Pro", "identifier": "synthetic.iphone16pro"}]}))
    elif args[:3] == ["simctl", "list", "runtimes"]:
        emit()
        print(json.dumps({"runtimes": [{"isAvailable": True, "version": "18.4", "identifier": "com.apple.CoreSimulator.SimRuntime.iOS-18-4"}]}))
    elif args[:4] == ["simctl", "list", "devices", "available"]:
        emit()
        print(json.dumps({"devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-18-4": [
                {"isAvailable": True, "name": "iPhone 16 Pro", "udid": "00000000-0000-4000-8000-000000000099"},
                {"isAvailable": True, "name": "iPhone 17 Pro", "udid": "00000000-0000-4000-8000-000000000099"},
            ]}}))
    elif args[:2] == ["simctl", "create"]:
        invocation = count("create")
        udid = "00000000-0000-4000-8000-" + f"{invocation:012d}"
        emit(udid=udid)
        print(udid)
    elif args[:2] == ["simctl", "bootstatus"]:
        invocation = count("bootstatus")
        rc = 9 if invocation == int(os.environ.get("QUIET_CHAIN_FAIL_BOOTSTATUS", "0")) else 0
        emit(rc, invocation=invocation)
        raise SystemExit(rc)
    elif args[:2] == ["simctl", "get_app_container"]:
        emit(1)
        raise SystemExit(1)
    elif args[:2] in (["simctl", "boot"], ["simctl", "shutdown"], ["simctl", "delete"], ["simctl", "install"], ["simctl", "uninstall"]):
        emit()
    elif args[:2] == ["xcresulttool", "merge"]:
        output = Path(args[args.index("--output-path") + 1])
        output.mkdir(parents=True)
        emit()
    else:
        reject("unexpected xcrun operation")
    raise SystemExit(0)

if tool == "xcodegen":
    if args != ["generate"]:
        reject("unexpected xcodegen operation")
    emit()
    raise SystemExit(0)

if tool == "xcodebuild":
    if not args or args[0] != "test":
        reject("unexpected xcodebuild operation")
    result = Path(args[args.index("-resultBundlePath") + 1])
    result.mkdir(parents=True)
    derived = Path(args[args.index("-derivedDataPath") + 1])
    product = derived / "Build/Products/Debug-iphonesimulator/QuietFixture.app"
    product.mkdir(parents=True, exist_ok=True)
    (product / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.quietfixture"}))
    is_ui = "-only-testing:QuietFixtureUITests" in args
    invocation = count("ui") if is_ui else count("unit")
    assertion = is_ui and os.environ.get("QUIET_CHAIN_UI_ASSERTION") == "1"
    ax = is_ui and invocation == 1 and os.environ.get("QUIET_CHAIN_AX_RETRY") == "1"
    rc = 65 if assertion or ax else 0
    emit(rc, phase="ui" if is_ui else "unit", invocation=invocation)
    if ax or assertion:
        print("Failed to initialize for UI testing: kAXErrorAPIDisabled")
    if assertion:
        print("Test Case 'QuietFixtureUITests.testProduct' failed.")
    else:
        print("synthetic test result")
    raise SystemExit(rc)

if tool == "maestro":
    if not args or args[0] != "test":
        reject("unexpected maestro operation")
    output = Path(args[args.index("--output") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('<testsuites><testsuite tests="1" failures="0"/></testsuites>')
    emit()
    raise SystemExit(0)

if tool == "judge":
    output = Path(args[args.index("--out") + 1])
    rc = 1 if os.environ.get("QUIET_CHAIN_UI_ASSERTION") == "1" else 0
    output.write_text(json.dumps({"verdict": "fail" if rc else "pass", "synthetic": True}))
    emit(rc)
    raise SystemExit(rc)

reject("unexpected tool")
'''


@unittest.skipUnless(Path("/bin/bash").exists() and Path("/usr/libexec/PlistBuddy").exists(), "byte-identical full verify chain needs macOS Bash and PlistBuddy; native tools remain stubbed")
class VerifyQuietChainTests(unittest.TestCase):
    def run_case(self, label: str, mode: str = "--full", **settings: str) -> tuple[subprocess.CompletedProcess, list[dict], Path]:
        evidence = os.environ.get("QUIET_CHAIN_EVIDENCE_DIR")
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            case = Path(tempfile.mkdtemp(prefix=label + "-", dir=evidence))
        else:
            temporary = tempfile.TemporaryDirectory(prefix="quiet-chain-" + label + "-")
            self.addCleanup(temporary.cleanup)
            case = Path(temporary.name)
        scripts = case / "toolkit/scripts"
        scripts.mkdir(parents=True)
        original = (ROOT / "scripts/verify.sh").read_bytes()
        copied = scripts / "verify.sh"
        copied.write_bytes(original)
        self.assertEqual(copied.read_bytes(), original)
        launcher = case / "launch.sh"
        launcher.write_text(LAUNCHER)
        stub = case / "stub.py"
        stub.write_text(STUB)
        repo = case / "app"
        repo.mkdir()
        (repo / "spec.json").write_text(json.dumps({
            "core_loop": {"reducer": "Quiet"},
            "required_suites": ["QuietModelTests", "QuietFixtureUITests"],
            "coverage_floor": 0.55,
        }))
        (repo / "maestro").mkdir()
        (repo / "maestro/flow.yaml").write_text("appId: com.example.quietfixture\n---\n- launchApp\n")
        (repo / "fixture.storekit").write_text("{}")
        env = {key: value for key, value in os.environ.items() if key not in ("BASH_ENV", "ENV") and not key.startswith(("QUIET_CHAIN_", "IOS_FULL_", "IOS_QUICK_", "BASH_FUNC_"))}
        # HOME is preserved verbatim; neither fixtures nor stubs write there.
        env.update({
            "QUIET_CHAIN_CASE": str(case),
            "QUIET_CHAIN_REAL_PYTHON": str(Path(sys.executable).resolve()),
            "QUIET_CHAIN_STUB": str(stub),
            "QUIET_CHAIN_VERIFY": str(copied),
            **settings,
        })
        self.assertEqual(env.get("HOME"), os.environ.get("HOME"))
        completed = subprocess.run(["/bin/bash", str(launcher), mode], cwd=repo, env=env, text=True, capture_output=True, timeout=45, check=False)
        (case / "stdout.log").write_text(completed.stdout)
        (case / "stderr.log").write_text(completed.stderr)
        events = [json.loads(line) for line in (case / "ledger.jsonl").read_text().splitlines()]
        (case / "receipt.json").write_text(json.dumps({
            "returncode": completed.returncode,
            "verify_sha256": hashlib.sha256(original).hexdigest(),
            "byte_identical": copied.read_bytes() == original,
            "synthetic_only": True,
            "settings": settings,
        }, indent=2))
        self.assertEqual((ROOT / "scripts/verify.sh").read_bytes(), original, "test harness changed production source")
        self.assertFalse([event for event in events if "unexpected" in event], str(case))
        return completed, events, case

    def assert_success(self, completed: subprocess.CompletedProcess, case: Path) -> None:
        self.assertEqual(completed.returncode, 0, f"{case}\n{completed.stdout}\n{completed.stderr}")

    def assert_test_args(self, events: list[dict], expected_only: list[str | None], full: bool = True) -> None:
        builds = [event for event in events if event["tool"] == "xcodebuild"]
        self.assertEqual(len(builds), len(expected_only))
        for event, only in zip(builds, expected_only):
            args = event["args"]
            self.assertEqual(args.count("-parallel-testing-enabled"), 1)
            self.assertEqual(args[args.index("-parallel-testing-enabled") + 1], "NO")
            self.assertEqual([arg for arg in args if arg.startswith("-only-testing:")], [only] if only else [])
            self.assertIn("CODE_SIGNING_ALLOWED=NO", args)
            if full:
                for flag, value in (("-test-timeouts-enabled", "YES"), ("-default-test-execution-time-allowance", "60"), ("-maximum-test-execution-time-allowance", "120")):
                    self.assertEqual(args[args.index(flag) + 1], value)
            else:
                self.assertNotIn("-test-timeouts-enabled", args)

    def assert_exact_quiet_gates(self, events: list[dict], count: int) -> None:
        quiet_events = [event for event in events if event["tool"] == "quiet"]
        self.assertEqual(len(quiet_events), count)
        for index, event in enumerate(events):
            if event["tool"] == "xcodebuild":
                previous = events[index - 1]
                self.assertEqual(previous["tool"], "quiet")
                destination = event["args"][event["args"].index("-destination") + 1]
                self.assertEqual(destination, "platform=iOS Simulator,id=" + previous["args"][0])
            if event["tool"] == "xcrun" and event["args"][:2] == ["simctl", "bootstatus"] and event["status"] == 0:
                following = events[index + 1]
                self.assertEqual(following["tool"], "quiet")
                self.assertEqual(following["args"], [event["args"][2]])
            if event["tool"] == "xcrun" and event["args"][:2] == ["simctl", "install"]:
                previous_quiet = next(candidate for candidate in reversed(events[:index]) if candidate["tool"] == "quiet")
                self.assertEqual(previous_quiet["args"], [event["args"][2]])
                between = events[events.index(previous_quiet) + 1:index]
                self.assertFalse([candidate for candidate in between if candidate["tool"] in ("xcodebuild", "maestro", "judge")])

    def assert_stopped_after_failure(self, events: list[dict], tool: str, invocation: int) -> None:
        failures = [event for event in events if event["tool"] == tool and event.get("invocation") == invocation and event["status"] != 0]
        self.assertEqual(len(failures), 1)
        after = events[events.index(failures[0]) + 1:]
        self.assertFalse([event for event in after if event["tool"] in ("quiet", "xcodebuild", "maestro", "judge")], after)
        self.assertFalse([event for event in after if event["tool"] == "xcrun" and event["args"][:2] == ["simctl", "install"]], after)
        self.assertTrue(all(event["tool"] == "xcrun" and event["args"][1] in ("shutdown", "delete") for event in after), after)

    def test_full_matrix_uses_two_exact_devices_and_maestro_gate(self) -> None:
        completed, events, case = self.run_case("full-green")
        self.assert_success(completed, case)
        self.assert_test_args(events, ["-only-testing:QuietFixtureTests", "-only-testing:QuietFixtureUITests"])
        self.assert_exact_quiet_gates(events, 5)
        creates = [event for event in events if event["tool"] == "xcrun" and event["args"][:2] == ["simctl", "create"]]
        self.assertEqual(len(creates), 2)
        builds = [event for event in events if event["tool"] == "xcodebuild"]
        self.assertNotEqual(builds[0]["args"][builds[0]["args"].index("-destination") + 1], builds[1]["args"][builds[1]["args"].index("-destination") + 1])
        self.assertEqual(len([event for event in events if event["tool"] == "maestro"]), 1)
        judge = [event for event in events if event["tool"] == "judge"][0]
        self.assertIn("--maestro", judge["args"])
        self.assertIn("--storekit", judge["args"])
        self.assertEqual(judge["args"][judge["args"].index("--coverage-floor") + 1], "0.55")
        self.assertEqual(events[-1]["args"][:2], ["simctl", "delete"])

    def test_ax_initialization_retry_gets_a_third_device_and_preserves_receipts(self) -> None:
        completed, events, case = self.run_case("ax-green", QUIET_CHAIN_AX_RETRY="1")
        self.assert_success(completed, case)
        self.assert_test_args(events, ["-only-testing:QuietFixtureTests", "-only-testing:QuietFixtureUITests", "-only-testing:QuietFixtureUITests"])
        self.assert_exact_quiet_gates(events, 7)
        creates = [event for event in events if event["tool"] == "xcrun" and event["args"][:2] == ["simctl", "create"]]
        self.assertEqual(len(creates), 3)
        self.assertEqual(len({event["udid"] for event in creates}), 3)
        verify = case / "app/.verify"
        self.assertIn("kAXErrorAPIDisabled", (verify / "build.ui.attempt1.ax-initialization.log").read_text())
        self.assertTrue((verify / "ui.attempt1.ax-initialization.xcresult").is_dir())

    def test_product_assertion_failure_is_not_retried(self) -> None:
        completed, events, case = self.run_case("ui-assertion", QUIET_CHAIN_UI_ASSERTION="1")
        self.assertEqual(completed.returncode, 1, str(case))
        self.assert_test_args(events, ["-only-testing:QuietFixtureTests", "-only-testing:QuietFixtureUITests"])
        self.assertFalse([event for event in events if event["tool"] == "maestro"])
        self.assertFalse((case / "app/.verify/ui.attempt1.ax-initialization.xcresult").exists())
        self.assertEqual(len([event for event in events if event["tool"] == "judge"]), 1)

    def test_fast_matrix_keeps_only_behavioral_oracle_and_no_maestro(self) -> None:
        completed, events, case = self.run_case("fast-green", mode="--fast")
        self.assert_success(completed, case)
        self.assert_test_args(events, ["-only-testing:QuietFixtureTests/QuietModelTests"], full=False)
        self.assert_exact_quiet_gates(events, 2)
        self.assertFalse([event for event in events if event["tool"] == "maestro"])
        fast_spec = json.loads((case / "app/.verify/fast-spec.json").read_text())
        self.assertEqual(fast_spec["required_suites"], ["QuietModelTests"])
        self.assertEqual(fast_spec["coverage_floor"], 0)
        judge = [event for event in events if event["tool"] == "judge"][0]
        self.assertIn("--no-coverage", judge["args"])

    def test_explicit_reused_full_device_keeps_original_all_tests_matrix(self) -> None:
        completed, events, case = self.run_case("reuse-green", IOS_FULL_REUSE_SIMULATOR="1")
        self.assert_success(completed, case)
        self.assert_test_args(events, [None])
        self.assert_exact_quiet_gates(events, 3)
        self.assertFalse([event for event in events if event["tool"] == "xcrun" and event["args"][:2] == ["simctl", "create"]])
        self.assertTrue(all(event["args"] == [EXISTING_UDID] for event in events if event["tool"] == "quiet"))

    def test_each_full_quiet_gate_failure_stops_all_later_test_stages(self) -> None:
        for invocation in range(1, 6):
            with self.subTest(invocation=invocation):
                completed, events, case = self.run_case(f"full-quiet-{invocation}-red", QUIET_CHAIN_FAIL_QUIET=str(invocation))
                self.assertEqual(completed.returncode, 2, str(case))
                self.assert_stopped_after_failure(events, "quiet", invocation)

    def test_each_additional_ax_retry_quiet_failure_stops_all_later_stages(self) -> None:
        for invocation in range(5, 8):
            with self.subTest(invocation=invocation):
                completed, events, case = self.run_case(f"ax-quiet-{invocation}-red", QUIET_CHAIN_AX_RETRY="1", QUIET_CHAIN_FAIL_QUIET=str(invocation))
                self.assertEqual(completed.returncode, 2, str(case))
                self.assert_stopped_after_failure(events, "quiet", invocation)

    def test_initial_ui_rebuild_and_ax_retry_bootstatus_failure_are_terminal(self) -> None:
        for invocation in range(1, 4):
            with self.subTest(invocation=invocation):
                completed, events, case = self.run_case(f"bootstatus-{invocation}-red", QUIET_CHAIN_AX_RETRY="1", QUIET_CHAIN_FAIL_BOOTSTATUS=str(invocation))
                self.assertEqual(completed.returncode, 9, str(case))
                self.assert_stopped_after_failure(events, "xcrun", invocation)

    def test_fast_initial_and_pre_xcodebuild_quiet_failures_are_terminal(self) -> None:
        for invocation in (1, 2):
            with self.subTest(invocation=invocation):
                completed, events, case = self.run_case(f"fast-quiet-{invocation}-red", mode="--fast", QUIET_CHAIN_FAIL_QUIET=str(invocation))
                self.assertEqual(completed.returncode, 2, str(case))
                self.assert_stopped_after_failure(events, "quiet", invocation)


if __name__ == "__main__":
    unittest.main()
