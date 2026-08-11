from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "scripts" / "prompt_corpus_audit.py"
SYNC_PATH = ROOT / "scripts" / "prompt_corpus_sync.py"

if "prompt_corpus_audit" not in sys.modules:
    audit_spec = importlib.util.spec_from_file_location(
        "prompt_corpus_audit", AUDIT_PATH
    )
    assert audit_spec and audit_spec.loader
    audit_module = importlib.util.module_from_spec(audit_spec)
    sys.modules[audit_spec.name] = audit_module
    audit_spec.loader.exec_module(audit_module)

sync_spec = importlib.util.spec_from_file_location(
    "prompt_corpus_sync", SYNC_PATH
)
assert sync_spec and sync_spec.loader
SYNC = importlib.util.module_from_spec(sync_spec)
sys.modules[sync_spec.name] = SYNC
sync_spec.loader.exec_module(SYNC)


class PromptCorpusSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.payload = [
            {
                "title": "One",
                "body": "Body {{count:int=5}}",
                "tags": ["A"],
            },
            {"title": "Two", "body": "Second", "tags": ["B"]},
        ]
        self.write(
            "canonical",
            "data.json",
            json.dumps(self.payload, ensure_ascii=False),
        )
        self.write(
            "chrome",
            "bundle.js",
            'const PROMPTS = [{"title":"Old","body":"Old","tags":[]}];\n'
            "const tail = 'must stay';\n",
        )
        self.write(
            "vscode",
            "data.json",
            json.dumps([{"title": "Old", "body": "Old", "tags": []}]),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, repo: str, relative: str, content: str) -> Path:
        path = self.workspace / repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def config(self) -> dict:
        return {
            "canonical": "canonical",
            "identity_fields": ["title"],
            "surfaces": [
                {
                    "id": "canonical",
                    "repo": "canonical",
                    "path": "data.json",
                    "format": "json-array",
                },
                {
                    "id": "chrome",
                    "repo": "chrome",
                    "path": "bundle.js",
                    "format": "js-const-array",
                    "marker": "const PROMPTS =",
                    "writable": True,
                    "sync": {"indent": 2},
                },
                {
                    "id": "vscode",
                    "repo": "vscode",
                    "path": "data.json",
                    "format": "json-array",
                    "writable": True,
                    "sync": {"indent": 2},
                },
            ],
        }

    def test_dry_run_describes_changes_without_touching_files(self) -> None:
        chrome_path = self.workspace / "chrome" / "bundle.js"
        before = chrome_path.read_text()

        plan, report = SYNC.sync(
            self.config(),
            self.workspace,
            {"chrome", "vscode"},
            write=False,
        )

        self.assertIsNone(report)
        self.assertEqual(chrome_path.read_text(), before)
        self.assertEqual(
            {item["surface"] for item in plan},
            {"chrome", "vscode"},
        )
        self.assertTrue(all(item["changed"] for item in plan))
        self.assertTrue(all(item["prompt_count"] == 2 for item in plan))

    def test_write_preserves_javascript_tail_and_converges(self) -> None:
        plan, report = SYNC.sync(
            self.config(),
            self.workspace,
            {"chrome", "vscode"},
            write=True,
        )

        self.assertIsNotNone(report)
        self.assertTrue(all(item["changed"] for item in plan))
        chrome_source = (self.workspace / "chrome" / "bundle.js").read_text()
        self.assertIn("const tail = 'must stay';", chrome_source)
        self.assertIn('"title": "Two"', chrome_source)
        self.assertEqual(
            json.loads(
                (self.workspace / "vscode" / "data.json").read_text()
            ),
            self.payload,
        )
        self.assertFalse(report.invalid)
        self.assertFalse(report.has_drift)

        second_plan, _ = SYNC.sync(
            self.config(),
            self.workspace,
            {"chrome", "vscode"},
            write=False,
        )
        self.assertTrue(all(not item["changed"] for item in second_plan))

    def test_write_preserves_full_width_punctuation_and_unicode_composition(
        self,
    ) -> None:
        decomposed_cafe = "Cafe\u0301"
        raw_payload = [
            {
                "title": "邮件起草（专业语气）  ",
                "body": (
                    "要求：自然口语化，保留专业术语。\n"
                    f"不要改写这个组合：{decomposed_cafe}  "
                ),
                "tags": [" 中文标签 ", "中文标签", "ＡＩ"],
            }
        ]
        canonical_path = self.workspace / "canonical" / "data.json"
        canonical_path.write_text(
            json.dumps(raw_payload, ensure_ascii=False),
            encoding="utf-8",
        )

        _, report = SYNC.sync(
            self.config(),
            self.workspace,
            {"vscode"},
            write=True,
        )
        generated = json.loads(
            (self.workspace / "vscode" / "data.json").read_text(
                encoding="utf-8"
            )
        )[0]

        self.assertEqual(generated["title"], "邮件起草（专业语气）")
        self.assertIn("要求：自然口语化，保留专业术语。", generated["body"])
        self.assertTrue(generated["body"].endswith(decomposed_cafe))
        self.assertNotEqual(
            generated["body"],
            unicodedata.normalize("NFC", generated["body"]),
            "sync must not compose/decompose canonical Unicode",
        )
        self.assertEqual(generated["tags"], ["中文标签", "ＡＩ"])
        self.assertFalse(report.invalid)
        self.assertFalse(report.has_drift)

    def test_refuses_non_writable_and_canonical_targets(self) -> None:
        config = self.config()
        config["surfaces"][1]["writable"] = False
        with self.assertRaisesRegex(SYNC.SyncError, "not opted in"):
            SYNC.sync(config, self.workspace, {"chrome"}, write=True)
        with self.assertRaisesRegex(SYNC.SyncError, "canonical"):
            SYNC.sync(
                self.config(),
                self.workspace,
                {"canonical"},
                write=True,
            )

    def test_invalid_canonical_never_writes_targets(self) -> None:
        canonical = self.workspace / "canonical" / "data.json"
        canonical.write_text(
            json.dumps(
                [
                    {"title": "Same", "body": "Body"},
                    {"title": "Same", "body": "Body"},
                ]
            )
        )
        target = self.workspace / "chrome" / "bundle.js"
        before = target.read_text()

        with self.assertRaisesRegex(
            SYNC.SyncError, "canonical surface is invalid"
        ):
            SYNC.sync(
                self.config(),
                self.workspace,
                {"chrome"},
                write=True,
            )

        self.assertEqual(target.read_text(), before)

    def test_missing_or_unterminated_target_array_fails_closed(self) -> None:
        path = self.workspace / "chrome" / "bundle.js"
        path.write_text("const PROMPTS = [", encoding="utf-8")
        with self.assertRaisesRegex(SYNC.SyncError, "unterminated"):
            SYNC.sync(
                self.config(),
                self.workspace,
                {"chrome"},
                write=True,
            )


if __name__ == "__main__":
    unittest.main()
