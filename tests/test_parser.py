"""Unit tests for turning typed text into structured commands."""

import unittest

from sabel.parser import Command, parse_command


class ParseCommandTests(unittest.TestCase):
    def test_parses_help(self) -> None:
        self.assertEqual(parse_command("help"), Command("help"))

    def test_parses_quit(self) -> None:
        self.assertEqual(parse_command("quit"), Command("quit"))

    def test_parses_exit_as_quit(self) -> None:
        self.assertEqual(parse_command("exit"), Command("quit"))

    def test_parses_open_website(self) -> None:
        self.assertEqual(
            parse_command("open website youtube.com"),
            Command("open_website", "youtube.com"),
        )

    def test_parses_open_application(self) -> None:
        self.assertEqual(
            parse_command("open app Safari"),
            Command("open_application", "Safari"),
        )

    def test_preserves_spaces_in_application_name(self) -> None:
        self.assertEqual(
            parse_command("open application Visual Studio Code"),
            Command("open_application", "Visual Studio Code"),
        )

    def test_handles_empty_input(self) -> None:
        self.assertEqual(parse_command("   "), Command("empty"))

    def test_handles_unknown_command(self) -> None:
        self.assertEqual(
            parse_command("tell me a joke"),
            Command("unknown", "tell me a joke"),
        )


if __name__ == "__main__":
    unittest.main()

