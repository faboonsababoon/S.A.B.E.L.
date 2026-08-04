"""Tests proving local built-ins bypass OpenAI entirely."""

import unittest
from unittest.mock import Mock

from main import handle_text
from sabel.ai_router import RouterResult


class LocalBypassTests(unittest.TestCase):
    def test_help_bypasses_api(self) -> None:
        ai_router = Mock()
        message, should_quit = handle_text("help", ai_router=ai_router)
        self.assertIn("Available commands", message)
        self.assertFalse(should_quit)
        ai_router.assert_not_called()

    def test_quit_bypasses_api(self) -> None:
        ai_router = Mock()
        _, should_quit = handle_text("quit", ai_router=ai_router)
        self.assertTrue(should_quit)
        ai_router.assert_not_called()

    def test_exit_bypasses_api(self) -> None:
        ai_router = Mock()
        _, should_quit = handle_text("exit", ai_router=ai_router)
        self.assertTrue(should_quit)
        ai_router.assert_not_called()

    def test_natural_language_uses_ai_router(self) -> None:
        ai_router = Mock(return_value=RouterResult("Opening YouTube."))
        message, should_quit = handle_text(
            "Could you open YouTube for me?", ai_router=ai_router
        )
        self.assertEqual(message, "Opening YouTube.")
        self.assertFalse(should_quit)
        ai_router.assert_called_once()


if __name__ == "__main__":
    unittest.main()

