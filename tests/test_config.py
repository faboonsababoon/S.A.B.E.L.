import unittest

from sabel.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        settings = load_settings({})
        self.assertEqual(settings.ollama_model, "qwen3:1.7b")
        self.assertEqual(settings.cloud_mode, "ask")
        self.assertEqual(settings.openai_model, "gpt-5.6-luna")
        self.assertEqual(settings.history_limit, 10)
        self.assertEqual(settings.pending_action_ttl, 60.0)

    def test_environment_and_cli_override(self):
        settings = load_settings(
            {
                "OLLAMA_MODEL": "local-test",
                "SABEL_CLOUD_MODE": "off",
                "OPENAI_MODEL": "cloud-test",
                "SABEL_HISTORY_LIMIT": "4",
                "SABEL_PENDING_ACTION_TTL": "30",
            },
            cloud_mode="auto",
            debug=True,
        )
        self.assertEqual(settings.ollama_model, "local-test")
        self.assertEqual(settings.cloud_mode, "auto")
        self.assertEqual(settings.history_limit, 4)
        self.assertEqual(settings.pending_action_ttl, 30.0)
        self.assertTrue(settings.debug)

    def test_invalid_cloud_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            load_settings({"SABEL_CLOUD_MODE": "always"})

    def test_keychain_fallback_and_environment_precedence(self):
        keychain_settings = load_settings({}, credential_loader=lambda: "sk-keychain-test")
        self.assertEqual(keychain_settings.openai_api_key, "sk-keychain-test")

        environment_settings = load_settings(
            {"OPENAI_API_KEY": "sk-environment-test"},
            credential_loader=lambda: "sk-keychain-test",
        )
        self.assertEqual(environment_settings.openai_api_key, "sk-environment-test")


if __name__ == "__main__":
    unittest.main()
