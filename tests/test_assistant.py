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

    def assistant(self, local_router, cloud_router):
        return SabelAssistant(
            self.settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=local_router,
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


if __name__ == "__main__":
    unittest.main()
