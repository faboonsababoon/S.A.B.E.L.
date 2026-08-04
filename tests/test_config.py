"""Unit tests for environment-based Phase 2 configuration."""

import unittest

from sabel.config import DEFAULT_MODEL, load_settings


class SettingsTests(unittest.TestCase):
    def test_uses_default_model(self) -> None:
        settings = load_settings({"OPENAI_API_KEY": "test-key"})
        self.assertEqual(settings.model, DEFAULT_MODEL)

    def test_uses_configured_model(self) -> None:
        settings = load_settings(
            {
                "OPENAI_API_KEY": "test-key",
                "SABEL_OPENAI_MODEL": "example-model",
            }
        )
        self.assertEqual(settings.model, "example-model")

    def test_missing_key_is_recorded_without_crashing(self) -> None:
        self.assertIsNone(load_settings({}).api_key)


if __name__ == "__main__":
    unittest.main()

