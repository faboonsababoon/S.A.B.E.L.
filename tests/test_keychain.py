"""Tests for secure macOS Keychain integration."""

import subprocess
import unittest
from unittest.mock import Mock

from sabel.keychain import KeychainError, read_openai_api_key, store_openai_api_key


class KeychainTests(unittest.TestCase):
    def test_read_returns_secret_without_putting_it_in_arguments(self):
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="sk-test-secret\n", stderr="")
        )
        self.assertEqual(read_openai_api_key(runner), "sk-test-secret")
        arguments = runner.call_args.args[0]
        self.assertNotIn("sk-test-secret", arguments)

    def test_missing_key_is_not_an_error(self):
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 44, stdout="", stderr="missing")
        )
        self.assertIsNone(read_openai_api_key(runner))

    def test_store_sends_secret_over_stdin_not_process_arguments(self):
        runner = Mock(
            return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")
        )
        store_openai_api_key("sk-this-is-a-long-test-secret", runner)
        arguments = runner.call_args.args[0]
        self.assertNotIn("sk-this-is-a-long-test-secret", arguments)
        self.assertEqual(
            runner.call_args.kwargs["input"], "sk-this-is-a-long-test-secret\n"
        )

    def test_invalid_secret_is_rejected_before_keychain(self):
        runner = Mock()
        with self.assertRaises(KeychainError):
            store_openai_api_key("not-a-key", runner)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
