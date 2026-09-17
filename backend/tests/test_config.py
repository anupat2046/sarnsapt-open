from __future__ import annotations

import unittest
from unittest.mock import patch

from thailex_api.config import Settings


class SettingsTests(unittest.TestCase):
    def test_thaillm_environment_uses_safe_defaults(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "THAILEX_SELECTOR_MODE": "thaillm",
                "THAILLM_API_KEY": "test-key",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertTrue(settings.thaillm_ready)
        self.assertTrue(settings.llm_ready)
        self.assertEqual(
            settings.thaillm_model,
            "Pathumma-ThaiLLM-qwen3-8b-think-3.0.0",
        )
        self.assertEqual(settings.thaillm_base_url, "https://thaillm.or.th/api/v1")

    def test_unknown_selector_mode_is_rejected(self) -> None:
        with patch.dict(
            "os.environ", {"THAILEX_SELECTOR_MODE": "unknown"}, clear=True
        ):
            with self.assertRaises(ValueError):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
