from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asc_app_store_stage as stage  # noqa: E402


class AppStoreStageTests(unittest.TestCase):
    def test_semantic_version_orders_numeric_components(self) -> None:
        self.assertGreater(stage.semantic_version("1.0.22"), stage.semantic_version("1.0.9"))

    def test_copyable_metadata_sets_localized_release_note(self) -> None:
        resource = {
            "attributes": {
                "locale": "zh-Hans",
                "description": "描述",
                "keywords": "习惯,健康",
                "supportUrl": "https://example.com/support",
                "marketingUrl": "https://example.com/app",
                "promotionalText": None,
            }
        }
        attributes = stage.copyable_localization_attributes(resource)
        self.assertEqual(attributes["locale"], "zh-Hans")
        self.assertEqual(attributes["description"], "描述")
        self.assertIn("可靠性", attributes["whatsNew"])
        self.assertNotIn("promotionalText", attributes)

    def test_editable_states_do_not_include_review_or_sale(self) -> None:
        self.assertIn("PREPARE_FOR_SUBMISSION", stage.EDITABLE_STATES)
        self.assertNotIn("WAITING_FOR_REVIEW", stage.EDITABLE_STATES)
        self.assertNotIn("READY_FOR_SALE", stage.EDITABLE_STATES)

    def test_screenshot_type_sets_cover_current_required_sizes(self) -> None:
        self.assertIn("APP_IPHONE_67", stage.IPHONE_SCREENSHOT_TYPES)
        self.assertIn("APP_IPAD_PRO_3GEN_129", stage.IPAD_SCREENSHOT_TYPES)


if __name__ == "__main__":
    unittest.main()
