import unittest
from unittest.mock import Mock

from sabel.actions import ActionResult
from sabel.assistant import SabelAssistant
from sabel.cloud_router import CloudResult
from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.local_router import LocalDecision
from sabel.tool_dispatcher import ToolDispatcher


class AssistantIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(cloud_mode="ask", openai_api_key="test")
        self.state = ConversationState(6)
        self.handlers = {
            "open_website": Mock(return_value=ActionResult(True, "opened locally")),
            "open_application": Mock(return_value=ActionResult(True, "opened app locally")),
            "open_youtube_search": Mock(return_value=ActionResult(True, "searched YouTube locally")),
            "open_web_search": Mock(return_value=ActionResult(True, "searched web locally")),
            "empty_trash": Mock(return_value=ActionResult(True, "Trash emptied.")),
        }
        self.ollama = Mock()
        self.dispatcher = ToolDispatcher(self.settings, self.state, lambda: True, self.handlers)

    def assistant(self, local_router, cloud_router, pending_router=None):
        return SabelAssistant(
            self.settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=local_router,
            pending_router=pending_router,
            dispatcher=self.dispatcher,
            cloud_router=cloud_router,
        )

    def test_local_action_never_calls_cloud(self):
        local = Mock()
        local.route.return_value = LocalDecision("open_website", {"url": "youtube.com"}, "", 1.0)
        cloud = Mock()
        result = self.assistant(local, cloud).handle("Open YouTube")
        self.assertEqual(result.message, "opened locally")
        cloud.handle.assert_not_called()

    def test_cloud_failure_does_not_disable_next_local_command(self):
        local = Mock()
        local.route.side_effect = [
            LocalDecision(
                "delegate_to_openai",
                {"task": "Research laptops", "reason": "current", "requires_current_web_information": True},
                "",
                1.0,
            ),
            LocalDecision("open_application", {"application_name": "Notes"}, "", 1.0),
        ]
        cloud = Mock()
        cloud.handle.return_value = CloudResult("Cloud research unavailable; local commands still work")
        assistant = self.assistant(local, cloud)
        first = assistant.handle("Research laptops", lambda prompt: "yes")
        second = assistant.handle("Open Notes")
        self.assertIn("unavailable", first.message)
        self.assertEqual(second.message, "opened app locally")

    def test_pending_confirmation_executes_without_normal_rerouting(self):
        local = Mock()
        local.route.return_value = LocalDecision("empty_trash", {}, "", 1.0)
        pending = Mock()
        pending.route.return_value = LocalDecision(
            "confirm_pending_action", {}, "", 1.0
        )
        cloud = Mock()
        assistant = self.assistant(local, cloud, pending)

        warning = assistant.handle("Clear my Trash")
        confirmed = assistant.handle("Yes, do it")

        self.assertIn("permanently", warning.message)
        self.assertEqual(confirmed.message, "Trash emptied.")
        self.handlers["empty_trash"].assert_called_once_with()
        self.assertEqual(local.route.call_count, 1)
        pending.route.assert_called_once_with("Yes, do it")
        self.assertIsNone(self.state.pending_action)

    def test_pending_question_is_explained_and_history_kept(self):
        local = Mock()
        local.route.return_value = LocalDecision("empty_trash", {}, "", 1.0)
        pending = Mock()
        pending.route.return_value = LocalDecision(
            "explain_pending_action", {}, "", 1.0
        )
        assistant = self.assistant(local, Mock(), pending)

        assistant.handle("Clear my Trash")
        explanation = assistant.handle("what does that mean")

        self.assertIn("permanently deletes", explanation.message)
        self.assertIsNotNone(self.state.pending_action)
        self.handlers["empty_trash"].assert_not_called()
        self.assertEqual(self.state.messages()[-2]["content"], "what does that mean")
        self.assertEqual(self.state.messages()[-1]["content"], explanation.message)

    def test_cancellation_phrases_clear_pending(self):
        for text in ("Cancel", "Do not delete it"):
            with self.subTest(text=text):
                state = ConversationState(10)
                handlers = dict(self.handlers)
                handlers["empty_trash"] = Mock(
                    return_value=ActionResult(True, "Trash emptied.")
                )
                dispatcher = ToolDispatcher(
                    self.settings, state, lambda: True, handlers
                )
                state.set_pending("empty_trash", {}, "description", "warning")
                pending = Mock()
                pending.route.return_value = LocalDecision(
                    "cancel_pending_action", {}, "", 1.0
                )
                assistant = SabelAssistant(
                    self.settings,
                    ollama_client=self.ollama,
                    state=state,
                    local_router=Mock(),
                    pending_router=pending,
                    dispatcher=dispatcher,
                    cloud_router=Mock(),
                )

                result = assistant.handle(text)

                self.assertIn("cancelled", result.message)
                self.assertIsNone(state.pending_action)
                handlers["empty_trash"].assert_not_called()

    def test_unrelated_request_cancels_pending_then_routes_normally(self):
        self.state.set_pending("empty_trash", {}, "description", "warning")
        local = Mock()
        local.route.return_value = LocalDecision(
            "open_website", {"url": "youtube.com"}, "", 1.0
        )
        pending = Mock()
        pending.route.return_value = LocalDecision("route_new_request", {}, "", 1.0)

        result = self.assistant(local, Mock(), pending).handle("Open YouTube instead")

        self.assertEqual(
            result.message,
            "Cancelled the pending Trash action. opened locally",
        )
        self.assertIsNone(self.state.pending_action)
        self.handlers["empty_trash"].assert_not_called()
        self.handlers["open_website"].assert_called_once_with("youtube.com")

    def test_expired_pending_action_is_cleared_before_normal_route(self):
        self.state.set_pending(
            "empty_trash", {}, "description", "warning", created_at=0.0
        )
        local = Mock()
        local.route.return_value = LocalDecision(
            "show_capabilities", {}, "", 1.0
        )
        pending = Mock()

        result = self.assistant(local, Mock(), pending).handle("What can you do?")

        self.assertIn("expired and was cancelled", result.message)
        pending.route.assert_not_called()
        self.assertIsNone(self.state.pending_action)


if __name__ == "__main__":
    unittest.main()
