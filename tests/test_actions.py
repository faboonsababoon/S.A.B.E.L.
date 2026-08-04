"""Unit tests for validation and the macOS action boundary."""

import subprocess
import unittest
from unittest.mock import Mock

from sabel.actions import (
    empty_trash,
    get_trash_status,
    normalize_application_name,
    normalize_website,
    open_application,
    open_service,
    open_spotify_search,
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


class NormalizeApplicationTests(unittest.TestCase):
    def test_allowlisted_app_path_is_reduced_to_name_only(self) -> None:
        self.assertEqual(
            normalize_application_name("/Applications/Safari.app"), "Safari"
        )

    def test_arbitrary_app_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "paths are not accepted"):
            normalize_application_name("/tmp/DefinitelyNotSafe.app")

    def test_settings_alias_uses_actual_macos_application_name(self) -> None:
        self.assertEqual(normalize_application_name("settings"), "System Settings")


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
        self.assertEqual(
            result.message, "I could not find an application named Missing App."
        )

    def test_application_path_is_never_passed_to_open(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        result = open_application("/Applications/Safari.app", run_command=runner)
        self.assertTrue(result.success)
        runner.assert_called_once_with(
            ["open", "-a", "Safari"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotIn("/Applications", result.message)

    def test_arbitrary_application_path_executes_nothing(self) -> None:
        runner = Mock()
        result = open_application("/tmp/Evil.app", run_command=runner)
        self.assertFalse(result.success)
        runner.assert_not_called()

    def test_service_registry_owns_url_resolution(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        result = open_service("youtube", run_command=runner)
        self.assertEqual(result.message, "Opening YouTube.")
        runner.assert_called_once_with(
            ["open", "https://www.youtube.com/"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_unknown_service_fails_without_side_effect(self) -> None:
        runner = Mock()
        result = open_service("unknown service", run_command=runner)
        self.assertFalse(result.success)
        self.assertIn("do not recognize", result.message)
        runner.assert_not_called()

    def test_account_scoped_service_requires_browser_profile_bridge(self) -> None:
        runner = Mock()
        result = open_service("personal_gmail", run_command=runner)
        self.assertFalse(result.success)
        self.assertIn("do not recognize", result.message)
        runner.assert_not_called()

    def test_spotify_search_uses_fixed_application_and_encoded_query(self) -> None:
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        result = open_spotify_search("Circles & Friends", run_command=runner)
        self.assertTrue(result.success)
        self.assertEqual(
            runner.call_args.args[0],
            ["open", "-a", "Spotify", "spotify:search:Circles%20%26%20Friends"],
        )
        self.assertIn("Spotify results", result.message)

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

    def test_trash_status_counts_fixed_directory_without_side_effects(self) -> None:
        opener = Mock(return_value=42)
        lister = Mock(return_value=["one", "two", "three"])
        closer = Mock()

        result = get_trash_status(opener, lister, closer)

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Your Trash contains 3 items.")
        self.assertTrue(opener.call_args.args[0].endswith("/.Trash"))
        lister.assert_called_once_with(42)
        closer.assert_called_once_with(42)

    def test_trash_status_handles_permission_denial(self) -> None:
        result = get_trash_status(
            Mock(side_effect=PermissionError), Mock(), Mock()
        )
        self.assertFalse(result.success)
        self.assertIn("denied permission", result.message)


if __name__ == "__main__":
    unittest.main()
