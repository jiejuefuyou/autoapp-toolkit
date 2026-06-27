#!/usr/bin/env python3
from __future__ import annotations
"""
judge.py — deterministic verdict for an autoapp build (the AWS dual-pipeline "judge").

It does NOT re-run tests. It reads a machine-readable spec (spec.json) plus already-
collected facts (test results, the StoreKit contract, recorded snapshot goldens, the
Maestro E2E outcome) and emits a structured verdict.json. Exit 0 iff verdict == "pass".
That exit code is what the CI router consumes: pass -> auto-merge, fail -> AI/human.

Design: the pure decision lives in `evaluate(spec, results, facts)` with ZERO IO, so it
is dogfooded by `--selftest` (a known-good case must pass, a known-bad case must fail).
All IO (xcresult parsing, reading the .storekit / snapshot dir / maestro output) lives in
`main()` and feeds `evaluate`.

Usage (CI):
  judge.py --spec spec.json \
           --results results.json            # normalized {passed,coverage,suites,...}
           [--xcresult path/to/Foo.xcresult] # alternative source; parsed best-effort
           --storekit App/Resources/Config.storekit \
           --snapshot-baseline snapshots/ \
           --maestro ~/.maestro/tests \
           --coverage-floor 0.65 \
           --out verdict.json

Dogfood:
  judge.py --selftest      # exits 0 only if the good case passes AND the bad case fails
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from typing import Any


# --------------------------------------------------------------------------- core
def evaluate(spec: dict[str, Any], results: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Pure deterministic judgment. No IO. Returns a verdict dict."""
    met: list[str] = []
    failed: list[str] = []

    def check(ok: bool, label: str) -> None:
        (met if ok else failed).append(label)

    # 1. tests passed
    check(bool(results.get("passed")), "tests_passed")
    if results.get("failed_tests"):
        failed.append("failed_tests=" + ",".join(results["failed_tests"][:5]))

    # 2. coverage floor (spec floor wins if higher than CLI default). Skipped when the
    #    caller ran only a single suite (--no-coverage), where whole-app coverage is moot.
    floor = max(float(spec.get("coverage_floor", 0.0)), float(facts.get("coverage_floor", 0.0)))
    cov = results.get("coverage")
    if not facts.get("skip_coverage"):
        check(cov is not None and float(cov) >= floor, f"coverage>={floor:.2f} (got {cov})")

    # 3. every required test suite ran AND passed (StateModel / Snapshot / StoreKit oracles)
    suites = results.get("suites", {})
    for s in spec.get("required_suites", []):
        check(suites.get(s) == "passed", f"suite:{s}")

    # 4. IAP product-id contract: every spec product id present in the .storekit file
    sk_ids = set(facts.get("storekit_product_ids", []))
    for pid in spec.get("iap", {}).get("product_ids", []):
        check(pid in sk_ids, f"iap_id_in_storekit:{pid}")

    # 5. UI-snapshot oracle — gated ONLY when a snapshot baseline exists; if the snapshot
    #    layer isn't deployed (no goldens), the judge does not invent a failure for it.
    snaps = facts.get("snapshot_files", {})  # {name: size_bytes}
    if snaps:
        for screen in spec.get("screens", []):
            size = max((sz for nm, sz in snaps.items() if screen.lower() in nm.lower()), default=0)
            check(size > 0, f"snapshot:{screen}")

    # 6. Maestro E2E outcome (only gated when a maestro dir was supplied)
    mp = facts.get("maestro_passed")
    if mp is not None:
        check(bool(mp), "maestro_e2e_passed")

    # 7. each required accessibility id is actually exercised by the E2E flows (soft: only if known)
    a11y_ref = facts.get("a11y_referenced")
    if a11y_ref is not None:
        for aid in spec.get("accessibility_ids_required", []):
            check(aid in a11y_ref, f"a11y_exercised:{aid}")

    verdict = "pass" if not failed else "fail"
    return {
        "verdict": verdict,
        "app": spec.get("app"),
        "coverage": cov,
        "coverage_floor": floor,
        "criteria_met": met,
        "criteria_failed": failed,
    }


# ----------------------------------------------------------------------------- io
def _xcrun_json(args: list[str]) -> Any:
    out = subprocess.run(["xcrun", *args], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def parse_xcresult(path: str) -> dict[str, Any]:
    """Extract pass/fail, per-suite results, failed-test names + coverage from an .xcresult.

    Validated against Xcode 26's `xcresulttool get test-results {summary,tests}` + `xccov`.
    The deterministic core does NOT depend on this (CI can instead emit a normalized
    results.json); each stage degrades gracefully if a sub-tool is unavailable.
    """
    res: dict[str, Any] = {"passed": None, "coverage": None, "suites": {}, "failed_tests": []}

    # 1. overall verdict + failed-test names
    try:
        summary = _xcrun_json(["xcresulttool", "get", "test-results", "summary", "--path", path, "--format", "json"])
        res["passed"] = summary.get("result") == "Passed"
        for f in summary.get("testFailures", []) or []:
            nm = f.get("testName") or f.get("testIdentifier") or f.get("targetName")
            if nm:
                res["failed_tests"].append(nm)
    except Exception as e:  # noqa: BLE001 — summary is the one required source
        res["error"] = f"xcresult summary parse failed: {e}"
        return res

    # 2. per-suite pass/fail (walk the test tree; nodeType == "Test Suite")
    try:
        tree = _xcrun_json(["xcresulttool", "get", "test-results", "tests", "--path", path, "--format", "json"])

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("nodeType") == "Test Suite" and node.get("name"):
                    res["suites"][node["name"]] = "passed" if node.get("result") == "Passed" else "failed"
                if node.get("nodeType") == "Test Case" and node.get("result") not in (None, "Passed", "Skipped"):
                    res["failed_tests"].append(node.get("name", "?"))
                for c in (node.get("children") or node.get("testNodes") or []):
                    walk(c)
            elif isinstance(node, list):
                for c in node:
                    walk(c)

        walk(tree)
    except Exception:  # noqa: BLE001 — suites are best-effort
        pass

    # 3. line coverage (present only when tests ran with code coverage enabled)
    try:
        cov = _xcrun_json(["xccov", "view", "--report", "--json", path])
        lc = cov.get("lineCoverage")
        if isinstance(lc, (int, float)):
            res["coverage"] = float(lc)
    except Exception:  # noqa: BLE001 — coverage optional
        pass

    res["failed_tests"] = sorted(set(res["failed_tests"]))
    return res


def storekit_product_ids(path: str | None) -> list[str]:
    if not path or not os.path.isfile(path):
        return []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    ids: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            pid = node.get("productID") or node.get("productId")
            if isinstance(pid, str):
                ids.append(pid)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return ids


def snapshot_sizes(snapshot_dir: str | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if not snapshot_dir or not os.path.isdir(snapshot_dir):
        return out
    for p in glob.glob(os.path.join(snapshot_dir, "**", "*.png"), recursive=True):
        out[os.path.basename(p)] = os.path.getsize(p)
    return out


def maestro_passed(maestro_dir: str | None) -> bool | None:
    """Return True/False from a maestro junit report dir, or None if not supplied."""
    if not maestro_dir or not os.path.isdir(maestro_dir):
        return None
    reports = glob.glob(os.path.join(maestro_dir, "**", "*.xml"), recursive=True)
    if not reports:
        return None
    for r in reports:
        txt = open(r, encoding="utf-8", errors="ignore").read()
        if 'failures="0"' not in txt and "failures=\"0\"" not in txt:
            if "failure" in txt.lower():
                return False
    return True


def a11y_referenced(maestro_dir: str | None, flows_dir: str | None) -> set[str] | None:
    if not flows_dir or not os.path.isdir(flows_dir):
        return None
    refs: set[str] = set()
    for p in glob.glob(os.path.join(flows_dir, "**", "*.y*ml"), recursive=True):
        txt = open(p, encoding="utf-8", errors="ignore").read()
        for tok in txt.replace(":", " ").replace('"', " ").replace("'", " ").split():
            refs.add(tok)
    return refs


# ------------------------------------------------------------------------- selftest
def _selftest() -> int:
    spec = {
        "app": "Demo",
        "coverage_floor": 0.65,
        "required_suites": ["DemoTests", "DemoStateModelTests", "DemoSnapshotTests"],
        "iap": {"product_ids": ["com.demo.premium"]},
        "screens": ["Home", "Paywall"],
        "accessibility_ids_required": ["premium_cta"],
    }
    good_results = {
        "passed": True, "coverage": 0.71, "failed_tests": [],
        "suites": {"DemoTests": "passed", "DemoStateModelTests": "passed", "DemoSnapshotTests": "passed"},
    }
    good_facts = {
        "storekit_product_ids": ["com.demo.premium"],
        "snapshot_files": {"Home.png": 1200, "Paywall.png": 900},
        "maestro_passed": True,
        "a11y_referenced": {"premium_cta", "launchApp"},
    }
    bad_results = {
        "passed": False, "coverage": 0.30, "failed_tests": ["DemoTests/testBuy"],
        "suites": {"DemoTests": "failed", "DemoStateModelTests": "passed"},  # missing SnapshotTests
    }
    bad_facts = {
        "storekit_product_ids": [],                       # product id missing from contract
        "snapshot_files": {"Home.png": 0},                # Paywall snapshot absent / Home empty
        "maestro_passed": False,
        "a11y_referenced": set(),                          # premium_cta never exercised
    }
    good = evaluate(spec, good_results, good_facts)
    bad = evaluate(spec, bad_results, bad_facts)
    ok_good = good["verdict"] == "pass"
    ok_bad = bad["verdict"] == "fail"
    print(f"[selftest] known-good -> {good['verdict']} (expect pass)  {'OK' if ok_good else 'WRONG'}")
    print(f"[selftest] known-bad  -> {bad['verdict']} (expect fail)  {'OK' if ok_bad else 'WRONG'}")
    if not ok_bad:
        print("           bad case must fail on:", bad["criteria_failed"])
    if ok_good and ok_bad:
        print("[selftest] PASS — judge is trustworthy (good passes, bad fails).")
        return 0
    print("[selftest] FAIL — judge is NOT trustworthy.", file=sys.stderr)
    return 1


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic autoapp build judge")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--spec")
    ap.add_argument("--results")
    ap.add_argument("--xcresult")
    ap.add_argument("--storekit")
    ap.add_argument("--snapshot-baseline")
    ap.add_argument("--maestro")
    ap.add_argument("--flows", help="maestro flows dir (for a11y-id coverage)")
    ap.add_argument("--coverage-floor", type=float, default=0.0)
    ap.add_argument("--no-coverage", action="store_true", help="skip the coverage gate (single-suite --fast runs)")
    ap.add_argument("--out", default="verdict.json")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()

    if not a.spec:
        ap.error("--spec is required (or use --selftest)")
    spec = json.load(open(a.spec, encoding="utf-8"))

    if a.results:
        results = json.load(open(a.results, encoding="utf-8"))
    elif a.xcresult:
        results = parse_xcresult(a.xcresult)
    else:
        ap.error("one of --results / --xcresult is required")

    facts = {
        "coverage_floor": a.coverage_floor,
        "skip_coverage": a.no_coverage,
        "storekit_product_ids": storekit_product_ids(a.storekit),
        "snapshot_files": snapshot_sizes(a.snapshot_baseline),
        "maestro_passed": maestro_passed(a.maestro),
        "a11y_referenced": a11y_referenced(a.maestro, a.flows),
    }
    verdict = evaluate(spec, results, facts)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2, ensure_ascii=False)

    icon = "PASS" if verdict["verdict"] == "pass" else "FAIL"
    print(f"[judge] {verdict.get('app')}: {icon}  coverage={verdict.get('coverage')} floor={verdict.get('coverage_floor')}")
    for c in verdict["criteria_failed"]:
        print(f"  x {c}")
    return 0 if verdict["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
