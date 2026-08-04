"""Unit tests for the model-tool allowlist and local validation boundary."""

import unittest
from unittest.mock import Mock

from sabel.actions import ActionResult
from sabel.tool_executor import execute_tool_call


class ToolExecutorTests(unittest.TestCase):
    def test_website_is_validated_before_execution(self) -> None:
        website_action = Mock(return_value=ActionResult(True, "opened"))

        result = execute_tool_call(
            "open_website",
            '{"url": "youtube.com"}',
            website_action=website_action,
        )

        self.assertTrue(result.success)
        website_action.assert_called_once_with("https://youtube.com")

    def test_unsupported_scheme_is_rejected_without_execution(self) -> None:
        website_action = Mock()

        result = execute_tool_call(
            "open_website",
            '{"url": "javascript:alert(1)"}',
            website_action=website_action,
        )

        self.assertFalse(result.success)
        self.assertIn("Only http", result.message)
        website_action.assert_not_called()

    def test_application_name_with_spaces_is_preserved(self) -> None:
        application_action = Mock(return_value=ActionResult(True, "opened"))

        result = execute_tool_call(
            "open_application",
            '{"application_name": "Visual Studio Code"}',
            application_action=application_action,
        )

        self.assertTrue(result.success)
        application_action.assert_called_once_with("Visual Studio Code")

    def test_unknown_tool_is_rejected(self) -> None:
        result = execute_tool_call("run_shell", '{"command": "whoami"}')
        self.assertFalse(result.success)
        self.assertIn("unknown tool", result.message)

    def test_malformed_json_is_rejected(self) -> None:
        result = execute_tool_call("open_website", "{not-json")
        self.assertFalse(result.success)
        self.assertIn("malformed", result.message)

    def test_missing_required_argument_is_rejected(self) -> None:
        website_action = Mock()
        result = execute_tool_call(
            "open_website", "{}", website_action=website_action
        )
        self.assertFalse(result.success)
        website_action.assert_not_called()

    def test_additional_argument_is_rejected_locally(self) -> None:
        website_action = Mock()
        result = execute_tool_call(
            "open_website",
            '{"url": "youtube.com", "command": "whoami"}',
            website_action=website_action,
        )
        self.assertFalse(result.success)
        website_action.assert_not_called()


if __name__ == "__main__":
    unittest.main()

