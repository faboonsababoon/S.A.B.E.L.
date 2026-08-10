import unittest

from sabel.config import DEFAULT_OLLAMA_MODEL, load_settings


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        settings = load_settings({})
        self.assertEqual(settings.ollama_model, DEFAULT_OLLAMA_MODEL)
        self.assertEqual(settings.cloud_mode, "ask")
        self.assertEqual(settings.openai_model, "gpt-5.6-luna")
        self.assertEqual(settings.history_limit, 10)
        self.assertEqual(settings.pending_action_ttl, 60.0)
        self.assertEqual(settings.clarification_ttl, 60.0)
        self.assertIsNone(settings.default_music_service)
        self.assertEqual(settings.browser_max_actions, 15)

    def test_environment_and_cli_override(self):
        settings = load_settings(
            {
                "OLLAMA_MODEL": "local-test",
                "SABEL_CLOUD_MODE": "off",
                "OPENAI_MODEL": "cloud-test",
                "SABEL_HISTORY_LIMIT": "4",
                "SABEL_PENDING_ACTION_TTL": "30",
                "SABEL_CLARIFICATION_TTL": "45",
                "SABEL_DEFAULT_MUSIC_SERVICE": "spotify",
                "SABEL_BROWSER_BRIDGE_PORT": "9876",
                "SABEL_BROWSER_MAX_ACTIONS": "6",
                "SABEL_CONFIG_DIR": "/tmp/sabel-config-test",
                "SABEL_ALBERT_URL": "https://albert.example.edu/",
                "SABEL_DEFAULT_GMAIL_PROFILE": "nyu",
            },
            cloud_mode="auto",
            debug=True,
        )
        self.assertEqual(settings.ollama_model, "local-test")
        self.assertEqual(settings.cloud_mode, "auto")
        self.assertEqual(settings.history_limit, 4)
        self.assertEqual(settings.pending_action_ttl, 30.0)
        self.assertEqual(settings.clarification_ttl, 45.0)
        self.assertEqual(settings.default_music_service, "spotify")
        self.assertEqual(settings.browser_bridge_port, 9876)
        self.assertEqual(settings.browser_max_actions, 6)
        self.assertEqual(str(settings.browser_token_path), "/tmp/sabel-config-test/browser-token")
        self.assertEqual(settings.albert_url, "https://albert.example.edu/")
        self.assertEqual(settings.default_gmail_profile, "nyu")
        self.assertTrue(settings.debug)

    def test_invalid_cloud_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            load_settings({"SABEL_CLOUD_MODE": "always"})

    def test_invalid_music_service_is_rejected(self):
        with self.assertRaises(ValueError):
            load_settings({"SABEL_DEFAULT_MUSIC_SERVICE": "anything"})

    def test_default_search_engine_and_reference_ttl_are_validated(self):
        settings = load_settings(
            {
                "SABEL_DEFAULT_SEARCH_ENGINE": "duckduckgo",
                "SABEL_BROWSER_REFERENCE_TTL": "45",
            }
        )
        self.assertEqual(settings.default_search_engine, "duckduckgo")
        self.assertEqual(settings.browser_reference_ttl, 45.0)
        with self.assertRaisesRegex(ValueError, "Default search engine"):
            load_settings({"SABEL_DEFAULT_SEARCH_ENGINE": "youtube"})

    def test_invalid_browser_profile_and_albert_url_are_rejected(self):
        with self.assertRaises(ValueError):
            load_settings({"SABEL_DEFAULT_GMAIL_PROFILE": "work"})
        with self.assertRaises(ValueError):
            load_settings({"SABEL_ALBERT_URL": "http://example.edu/"})
        with self.assertRaises(ValueError):
            load_settings({"SABEL_BROWSER_BRIDGE_PORT": "70000"})

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
