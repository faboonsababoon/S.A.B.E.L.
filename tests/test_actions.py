"""Unit tests for validation and the macOS action boundary."""

import subprocess
import unittest
from unittest.mock import Mock

from sabel.actions import (
    empty_trash,
    normalize_website,
    open_application,
    open_web_search,
    open_website,
    open_youtube_search,
)


class NormalizeWebsiteTests(unittest.TestCase):
    def test_adds_https_when_scheme_is_missing(self) -> None:
        self.assertEqual(normalize_website("youtube.com"), "https://youtube.com")

    def test_preserves_existing_https(self) -> None:
        self.assertEqual(
            normalize_website("https://www.nyu.edu"),
            "https://www.nyu.edu",
        )

    def test_rejects_unsupported_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only http"):
            normalize_website("file:///etc/passwd")

    def test_rejects_shell_syntax(self) -> None:
        with self.assertRaisesRegex(ValueError, "command syntax"):
            normalize_website("https://example.com;whoami")

    def test_rejects_malformed_address(self) -> None:
        with self.assertRaisesRegex(ValueError, "malformed"):
            normalize_website("https://")


class OpenActionTests(unittest.TestCase):
    def test_website_uses_safe_argument_list(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess(
                ["open", "https://youtube.com"], 0, "", ""
            )
        )

        result = open_website("youtube.com", run_command=runner)

        self.assertTrue(result.success)
        runner.assert_called_once_with(
            ["open", "https://youtube.com"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_application_name_with_spaces_is_one_argument(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess(
                ["open", "-a", "Visual Studio Code"], 0, "", ""
            )
        )

        result = open_application("Visual Studio Code", run_command=runner)

        self.assertTrue(result.success)
        runner.assert_called_once_with(
            ["open", "-a", "Visual Studio Code"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_reports_application_open_failure(self) -> None:
        runner = Mock(
            return_value=subprocess.CompletedProcess(
                ["open", "-a", "Missing App"], 1, "", "not found"
            )
        )

        result = open_application("Missing App", run_command=runner)

        self.assertFalse(result.success)
        self.assertIn("not found", result.message)

    def test_youtube_search_is_url_encoded(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        open_youtube_search("SSundee official channel", run_command=runner)
        self.assertIn(
            "search_query=SSundee+official+channel",
            runner.call_args.args[0][1],
        )

    def test_web_search_is_url_encoded(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        open_web_search("NYU academic calendar", run_command=runner)
        self.assertIn("q=NYU+academic+calendar", runner.call_args.args[0][1])

    def test_empty_trash_uses_fixed_reviewed_command(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        result = empty_trash(run_command=runner)
        self.assertTrue(result.success)
        runner.assert_called_once_with(
            ["osascript", "-e", 'tell application "Finder" to empty trash'],
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
