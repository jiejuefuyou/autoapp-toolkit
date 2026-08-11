from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "prompt_corpus_audit.py"
SPEC = importlib.util.spec_from_file_location("prompt_corpus_audit", MODULE_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


class PromptCorpusAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, repo: str, relative: str, content: str) -> Path:
        path = self.workspace / repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def config(self, surfaces: list[dict], canonical: str = "canonical") -> dict:
        return {
            "canonical": canonical,
            "identity_fields": ["title"],
            "surfaces": surfaces,
        }

    def test_extracts_all_supported_formats_without_executing_javascript(self) -> None:
        payload = [{"title": "One", "body": "Hello {{name}}", "tags": ["x"]}]
        self.write("json", "data.json", json.dumps(payload))
        self.write(
            "const",
            "bundle.js",
            f"const PROMPTS = {json.dumps(payload)};\nthrow new Error('must never run');\n",
        )
        self.write(
            "commonjs",
            "prompts.js",
            f"module.exports = {json.dumps(payload)};\nprocess.exit(99);\n",
        )
        surfaces = [
            {"id": "canonical", "repo": "json", "path": "data.json", "format": "json-array"},
            {"id": "const", "repo": "const", "path": "bundle.js", "format": "js-const-array"},
            {"id": "commonjs", "repo": "commonjs", "path": "prompts.js", "format": "commonjs-array"},
        ]

        report = AUDIT.audit(self.config(surfaces), self.workspace)

        self.assertFalse(report.invalid)
        self.assertFalse(report.has_drift)
        self.assertEqual([item.count for item in report.surfaces], [1, 1, 1])

    def test_field_fallback_maps_localized_canonical_to_plain_surface(self) -> None:
        canonical = [
            {
                "title": "中文标题",
                "body": "中文正文",
                "tags": ["中文"],
                "title_en": "English title",
                "body_en": "English body {{count:int=5}}",
                "tags_en": ["English"],
            }
        ]
        plain = [
            {
                "title": "English title",
                "body": "English body {{count:int=5}}",
                "tags": ["English"],
            }
        ]
        self.write("ios", "starter.json", json.dumps(canonical, ensure_ascii=False))
        self.write("web", "prompts.json", json.dumps(plain, ensure_ascii=False))
        surfaces = [
            {
                "id": "canonical",
                "repo": "ios",
                "path": "starter.json",
                "fields": {
                    "title": ["title_en", "title"],
                    "body": ["body_en", "body"],
                    "tags": ["tags_en", "tags"],
                },
            },
            {"id": "web", "repo": "web", "path": "prompts.json"},
        ]

        report = AUDIT.audit(self.config(surfaces), self.workspace)

        self.assertFalse(report.invalid)
        self.assertFalse(report.has_drift)

    def test_reports_missing_extra_changed_fields_and_order(self) -> None:
        canonical = [
            {"title": "A", "body": "Body {{x:int=1}}", "tags": ["one"]},
            {"title": "B", "body": "Body B", "tags": ["two"]},
        ]
        candidate = [
            {"title": "B", "body": "Body B changed", "tags": ["two", "new"]},
            {"title": "C", "body": "Body C", "tags": []},
        ]
        self.write("a", "data.json", json.dumps(canonical))
        self.write("b", "data.json", json.dumps(candidate))
        surfaces = [
            {"id": "canonical", "repo": "a", "path": "data.json"},
            {"id": "candidate", "repo": "b", "path": "data.json"},
        ]

        report = AUDIT.audit(self.config(surfaces), self.workspace)
        drift = report.drift[0]

        self.assertTrue(report.has_drift)
        self.assertEqual(drift.missing, ["A"])
        self.assertEqual(drift.extra, ["C"])
        self.assertEqual(drift.changed[0]["title"], "B")
        self.assertEqual(drift.changed[0]["fields"], ["body", "tags"])
        self.assertFalse(drift.order_changed, "Only one common identity remains")

    def test_order_drift_is_reported_independently(self) -> None:
        canonical = [
            {"title": "A", "body": "A"},
            {"title": "B", "body": "B"},
        ]
        candidate = list(reversed(canonical))
        self.write("a", "data.json", json.dumps(canonical))
        self.write("b", "data.json", json.dumps(candidate))
        report = AUDIT.audit(
            self.config(
                [
                    {"id": "canonical", "repo": "a", "path": "data.json"},
                    {"id": "candidate", "repo": "b", "path": "data.json"},
                ]
            ),
            self.workspace,
        )
        self.assertTrue(report.drift[0].order_changed)

    def test_duplicate_identity_exact_duplicate_and_variable_conflict_are_invalid(self) -> None:
        payload = [
            {"title": "Same", "body": "{{x:int=1}} {{x:string=a}}"},
            {"title": "Same", "body": "{{x:int=1}} {{x:string=a}}"},
        ]
        self.write("a", "data.json", json.dumps(payload))
        self.write("b", "data.json", json.dumps(payload))
        report = AUDIT.audit(
            self.config(
                [
                    {"id": "canonical", "repo": "a", "path": "data.json"},
                    {"id": "candidate", "repo": "b", "path": "data.json"},
                ]
            ),
            self.workspace,
        )

        self.assertTrue(report.invalid)
        self.assertTrue(report.surfaces[0].duplicate_identities)
        self.assertTrue(report.surfaces[0].exact_duplicates)
        self.assertEqual(len(report.surfaces[0].variable_errors), 2)

    def test_invalid_surface_is_reported_not_crashed(self) -> None:
        self.write("a", "data.json", "not-json")
        self.write("b", "data.json", "[]")
        report = AUDIT.audit(
            self.config(
                [
                    {"id": "canonical", "repo": "a", "path": "data.json"},
                    {"id": "candidate", "repo": "b", "path": "data.json"},
                ]
            ),
            self.workspace,
        )
        self.assertTrue(report.invalid)
        self.assertTrue(report.surfaces[0].errors)

    def test_cli_writes_reports_and_honors_exit_policy(self) -> None:
        self.write("a", "data.json", json.dumps([{"title": "A", "body": "A"}]))
        self.write("b", "data.json", json.dumps([{"title": "B", "body": "B"}]))
        config_path = self.workspace / "config.json"
        config_path.write_text(
            json.dumps(
                self.config(
                    [
                        {"id": "canonical", "repo": "a", "path": "data.json"},
                        {"id": "candidate", "repo": "b", "path": "data.json"},
                    ]
                )
            ),
            encoding="utf-8",
        )
        json_report = self.workspace / "report.json"
        text_report = self.workspace / "report.txt"

        failed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "--config",
                str(config_path),
                "--workspace",
                str(self.workspace),
                "--json",
                str(json_report),
                "--text",
                str(text_report),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        allowed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "--config",
                str(config_path),
                "--workspace",
                str(self.workspace),
                "--fail-on",
                "never",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(failed.returncode, 1)
        self.assertEqual(allowed.returncode, 0)
        self.assertTrue(json_report.is_file())
        self.assertTrue(text_report.is_file())
        self.assertTrue(json.loads(json_report.read_text())["has_drift"])
        self.assertIn("missing=1", text_report.read_text())


if __name__ == "__main__":
    unittest.main()
