import unittest
from unittest.mock import Mock

from sabel.actions import ActionResult
from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.tool_dispatcher import ToolDispatcher


class ToolDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.state = ConversationState(6)
        self.handlers = {
            "open_website": Mock(return_value=ActionResult(True, "website opened")),
            "open_application": Mock(return_value=ActionResult(True, "app opened")),
            "open_youtube_search": Mock(return_value=ActionResult(True, "YouTube search opened")),
            "open_web_search": Mock(return_value=ActionResult(True, "web search opened")),
            "empty_trash": Mock(return_value=ActionResult(True, "Trash emptied.")),
        }
        self.dispatcher = ToolDispatcher(Settings(), self.state, lambda: True, self.handlers)

    def test_local_actions_dispatch_without_openai(self):
        result = self.dispatcher.dispatch("open_application", {"application_name": "Visual Studio Code"}, "Open VSC")
        self.assertEqual(result.message, "app opened")
        self.handlers["open_application"].assert_called_once_with("Visual Studio Code")

    def test_unknown_and_extra_arguments_are_rejected(self):
        self.assertIn("unknown", self.dispatcher.dispatch("run_shell", {}, "x").message)
        result = self.dispatcher.dispatch("open_website", {"url": "youtube.com", "command": "rm"}, "x")
        self.assertIn("rejected", result.message)
        self.handlers["open_website"].assert_not_called()

    def test_trash_requires_explicit_second_confirmation(self):
        warning = self.dispatcher.dispatch("empty_trash", {}, "Clear my Trash")
        self.assertIn("permanently", warning.message)
        self.assertEqual(self.state.pending_action.tool_name, "empty_trash")
        self.handlers["empty_trash"].assert_not_called()

        vague = self.dispatcher.dispatch_pending("confirm_pending_action", {}, "okay")
        self.assertIn("continue", vague.message)
        self.handlers["empty_trash"].assert_not_called()

        confirmed = self.dispatcher.dispatch_pending("confirm_pending_action", {}, "Yes, empty the Trash")
        self.assertEqual(confirmed.message, "Trash emptied.")
        self.handlers["empty_trash"].assert_called_once()
        self.assertIsNone(self.state.pending_action)

    def test_cancellation_clears_pending_action(self):
        self.dispatcher.dispatch("empty_trash", {}, "Clear Trash")
        result = self.dispatcher.dispatch_pending("cancel_pending_action", {}, "Never mind")
        self.assertIn("cancelled", result.message)
        self.assertIsNone(self.state.pending_action)

    def test_explanation_keeps_pending_action(self):
        self.dispatcher.dispatch("empty_trash", {}, "Clear Trash")
        result = self.dispatcher.dispatch_pending(
            "explain_pending_action", {}, "Why do I need to confirm?"
        )
        self.assertIn("permanently deletes", result.message)
        self.assertIsNotNone(self.state.pending_action)
        self.handlers["empty_trash"].assert_not_called()

    def test_redundant_confirmation_field_is_normalized_but_unknown_is_rejected(self):
        self.dispatcher.dispatch("empty_trash", {}, "Clear Trash")
        accepted = self.dispatcher.dispatch_pending(
            "confirm_pending_action",
            {"action": "empty_trash"},
            "Yes, do it",
        )
        self.assertEqual(accepted.message, "Trash emptied.")
        self.handlers["empty_trash"].assert_called_once()

        self.handlers["empty_trash"].reset_mock()
        self.dispatcher.dispatch("empty_trash", {}, "Clear Trash")
        rejected = self.dispatcher.dispatch_pending(
            "confirm_pending_action", {"force": True}, "Yes, do it"
        )
        self.assertIn("rejected", rejected.message)
        self.handlers["empty_trash"].assert_not_called()
        self.assertIsNotNone(self.state.pending_action)

    def test_confirmation_uses_stored_arguments_and_clears_after_failure(self):
        handler = Mock(return_value=ActionResult(False, "operation failed"))
        self.dispatcher.handlers["reviewed_action"] = handler
        self.state.set_pending(
            "reviewed_action",
            {"target": "stored-original"},
            "description",
            "warning",
        )

        result = self.dispatcher.dispatch_pending(
            "confirm_pending_action", {}, "I confirm"
        )

        self.assertEqual(result.message, "operation failed")
        handler.assert_called_once_with(target="stored-original")
        self.assertIsNone(self.state.pending_action)

    def test_unrelated_request_clears_pending_without_executing(self):
        self.dispatcher.dispatch("empty_trash", {}, "Clear Trash")
        result = self.dispatcher.dispatch_pending(
            "route_new_request", {}, "Open YouTube"
        )
        self.assertTrue(result.continue_with_new_request)
        self.assertIsNone(self.state.pending_action)
        self.handlers["empty_trash"].assert_not_called()

    def test_delegation_is_data_not_a_local_action(self):
        result = self.dispatcher.dispatch(
            "delegate_to_openai",
            {"task": "Compare laptops", "reason": "current comparison", "requires_current_web_information": True},
            "research laptops",
        )
        self.assertEqual(result.delegation.task, "Compare laptops")
        for handler in self.handlers.values():
            handler.assert_not_called()

    def test_research_source_is_validated_and_range_checked(self):
        self.state.store_research("answer", ["https://example.com/one"])
        opened = self.dispatcher.dispatch("open_research_source", {"source_number": 1}, "Open first")
        self.assertEqual(opened.message, "website opened")
        self.handlers["open_website"].assert_called_once_with("https://example.com/one")
        missing = self.dispatcher.dispatch("open_research_source", {"source_number": 2}, "Open second")
        self.assertIn("not available", missing.message)

    def test_status_never_contains_key(self):
        settings = Settings(openai_api_key="secret-value")
        dispatcher = ToolDispatcher(settings, self.state, lambda: True, self.handlers)
        message = dispatcher.dispatch("show_status", {}, "status").message
        self.assertIn("OpenAI configured: Yes", message)
        self.assertNotIn("secret-value", message)


if __name__ == "__main__":
    unittest.main()
