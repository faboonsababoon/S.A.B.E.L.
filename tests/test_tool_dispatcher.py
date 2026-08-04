import unittest
from unittest.mock import Mock

from sabel.actions import ActionResult
from sabel.browser_copilot import BrowserOutcome
from sabel.browser_models import BrowserActionState
from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.tool_dispatcher import ToolDispatcher


class ToolDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.state = ConversationState(6)
        self.handlers = {
            "open_service": Mock(return_value=ActionResult(True, "Opening YouTube.")),
            "open_website": Mock(return_value=ActionResult(True, "website opened")),
            "open_application": Mock(return_value=ActionResult(True, "app opened")),
            "open_spotify_search": Mock(return_value=ActionResult(True, "Spotify results opened")),
            "open_youtube_search": Mock(return_value=ActionResult(True, "YouTube search opened")),
            "open_web_search": Mock(return_value=ActionResult(True, "web search opened")),
            "empty_trash": Mock(return_value=ActionResult(True, "Trash emptied.")),
            "get_trash_status": Mock(
                return_value=ActionResult(True, "Your Trash contains 3 items.")
            ),
        }
        self.dispatcher = ToolDispatcher(Settings(), self.state, lambda: True, self.handlers)

    def test_local_actions_dispatch_without_openai(self):
        result = self.dispatcher.dispatch("open_application", {"application_name": "Visual Studio Code"}, "Open VSC")
        self.assertEqual(result.message, "app opened")
        self.handlers["open_application"].assert_called_once_with("Visual Studio Code")

    def test_application_arguments_reject_paths_and_forbidden_fields(self):
        arbitrary = self.dispatcher.dispatch(
            "open_application", {"application_name": "/tmp/Evil.app"}, "open it"
        )
        path_field = self.dispatcher.dispatch(
            "open_application", {"path": "/Applications/Safari.app"}, "open it"
        )
        command_field = self.dispatcher.dispatch(
            "open_application", {"application_name": "Safari", "command": "whoami"}, "open it"
        )
        self.assertIn("paths are not accepted", arbitrary.message)
        self.assertIn("rejected", path_field.message)
        self.assertIn("rejected", command_field.message)
        self.handlers["open_application"].assert_not_called()

    def test_allowlisted_application_path_is_normalized_before_handler(self):
        result = self.dispatcher.dispatch(
            "open_application",
            {"application_name": "/Applications/Safari.app"},
            "open Safari",
        )
        self.assertEqual(result.validated_arguments, {"application_name": "Safari"})
        self.handlers["open_application"].assert_called_once_with("Safari")

    def test_services_resolve_by_registry_and_unknown_names_fail_safely(self):
        opened = self.dispatcher.dispatch(
            "open_service", {"service_name": "YouTube"}, "go to YouTube"
        )
        self.assertEqual(opened.message, "Opening YouTube.")
        self.handlers["open_service"].assert_called_once_with("youtube", "")

        self.handlers["open_service"].reset_mock()
        unknown = self.dispatcher.dispatch(
            "open_service", {"service_name": "made-up"}, "open it"
        )
        self.assertIn("do not recognize", unknown.message)
        self.handlers["open_service"].assert_not_called()

    def test_account_scoped_service_cannot_bypass_profile_bridge(self):
        result = self.dispatcher.dispatch(
            "open_service", {"service_name": "personal_gmail"}, "open my Gmail"
        )
        self.assertIn("do not recognize", result.message)
        self.handlers["open_service"].assert_not_called()

    def test_unknown_and_extra_arguments_are_rejected(self):
        self.assertIn("unknown", self.dispatcher.dispatch("run_shell", {}, "x").message)
        result = self.dispatcher.dispatch("open_website", {"url": "youtube.com", "command": "rm"}, "x")
        self.assertIn("rejected", result.message)
        self.handlers["open_website"].assert_not_called()

    def test_trash_requires_explicit_second_confirmation(self):
        warning = self.dispatcher.dispatch("empty_trash", {}, "Clear my Trash")
        self.assertIn("permanently", warning.message)
        self.assertEqual(
            self.state.pending_destructive_action.tool_name, "empty_trash"
        )
        self.handlers["empty_trash"].assert_not_called()

        vague = self.dispatcher.dispatch_pending("confirm_pending_action", {}, "okay")
        self.assertIn("continue", vague.message)
        self.handlers["empty_trash"].assert_not_called()

        confirmed = self.dispatcher.dispatch_pending("confirm_pending_action", {}, "Yes, empty the Trash")
        self.assertEqual(confirmed.message, "Trash emptied.")
        self.handlers["empty_trash"].assert_called_once()
        self.assertIsNone(self.state.pending_destructive_action)

    def test_cancellation_clears_pending_action(self):
        self.dispatcher.dispatch("empty_trash", {}, "Clear Trash")
        result = self.dispatcher.dispatch_pending("cancel_pending_action", {}, "Never mind")
        self.assertIn("cancelled", result.message)
        self.assertIsNone(self.state.pending_destructive_action)

    def test_explanation_keeps_pending_action(self):
        self.dispatcher.dispatch("empty_trash", {}, "Clear Trash")
        result = self.dispatcher.dispatch_pending(
            "explain_pending_action", {}, "Why do I need to confirm?"
        )
        self.assertIn("permanently deletes", result.message)
        self.assertIsNotNone(self.state.pending_destructive_action)
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
        self.assertIsNotNone(self.state.pending_destructive_action)

    def test_confirmation_uses_stored_arguments_and_clears_after_failure(self):
        handler = Mock(return_value=ActionResult(False, "operation failed"))
        self.dispatcher.handlers["reviewed_action"] = handler
        self.state.set_pending_destructive_action(
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
        self.assertIsNone(self.state.pending_destructive_action)

    def test_unrelated_request_clears_pending_without_executing(self):
        self.dispatcher.dispatch("empty_trash", {}, "Clear Trash")
        result = self.dispatcher.dispatch_pending(
            "route_new_request", {}, "Open YouTube"
        )
        self.assertTrue(result.continue_with_new_request)
        self.assertIsNone(self.state.pending_destructive_action)
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
        self.assertIn("Ollama: Available", message)
        self.assertIn("Pending clarification: None", message)

    def test_trash_status_is_distinct_from_sabel_status(self):
        trash = self.dispatcher.dispatch(
            "get_trash_status", {}, "Is my Trash empty?"
        )
        runtime = self.dispatcher.dispatch("show_status", {}, "Show status")
        self.assertEqual(trash.message, "Your Trash contains 3 items.")
        self.assertIn("SABEL status", runtime.message)
        self.handlers["get_trash_status"].assert_called_once_with()

    def test_high_level_browser_tools_render_typed_outcomes(self):
        browser = Mock()
        browser.open_service_in_profile.return_value = BrowserOutcome(
            BrowserActionState.EXECUTED,
            "Opening YouTube in your Personal Chrome profile.",
            True,
        )
        dispatcher = ToolDispatcher(
            Settings(), self.state, lambda: True, self.handlers, browser
        )
        result = dispatcher.dispatch(
            "open_service_in_profile",
            {"service": "youtube", "profile": "personal"},
            "Open YouTube in Personal",
        )
        self.assertEqual(
            result.message, "Opening YouTube in your Personal Chrome profile."
        )
        self.assertEqual(result.action_state, "executed")
        self.assertFalse(result.verified)
        browser.open_service_in_profile.assert_called_once_with(
            "youtube", "personal"
        )

    def test_browser_result_clarification_and_argument_validation(self):
        browser = Mock()
        browser.open_service_in_profile.return_value = BrowserOutcome(
            BrowserActionState.PLANNED,
            "Which Gmail account should I use: Personal or NYU?",
            False,
            clarification="Which Gmail account should I use: Personal or NYU?",
        )
        dispatcher = ToolDispatcher(
            Settings(), self.state, lambda: True, self.handlers, browser
        )
        result = dispatcher.dispatch(
            "open_service_in_profile", {"service": "gmail"}, "Open Gmail"
        )
        self.assertEqual(result.clarification_intent, "open_service_in_profile")
        self.assertEqual(result.clarification_missing, ["profile"])

        rejected = dispatcher.dispatch(
            "browser_copilot_task",
            {
                "objective": "Open Taz Skylar's YouTube channel",
                "service": "youtube",
                "profile": "personal",
                "selector": "#unsafe",
            },
            "open it",
        )
        self.assertIn("rejected", rejected.message)
        browser.browser_copilot_task.assert_not_called()

    def test_low_level_browser_commands_are_not_dispatchable(self):
        result = self.dispatcher.dispatch(
            "browser_click",
            {"tab_id": 1, "snapshot_id": "s", "element_id": "e"},
            "click",
        )
        self.assertIn("unknown", result.message)

    def test_tab_listing_requires_an_explicit_profile(self):
        browser = Mock()
        browser.show_browser_tabs.return_value = BrowserOutcome(
            BrowserActionState.VERIFIED,
            "Tabs in Personal\nYouTube — youtube.com (active)",
            True,
            True,
        )
        dispatcher = ToolDispatcher(
            Settings(), self.state, lambda: True, self.handlers, browser
        )
        rejected = dispatcher.dispatch("show_browser_tabs", {}, "show tabs")
        shown = dispatcher.dispatch(
            "show_browser_tabs", {"profile": "Personal"}, "show browser tabs in Personal"
        )
        self.assertIn("rejected", rejected.message)
        self.assertTrue(shown.verified)
        browser.show_browser_tabs.assert_called_once_with("personal")


if __name__ == "__main__":
    unittest.main()
