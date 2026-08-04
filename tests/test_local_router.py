import unittest
from unittest.mock import Mock

from sabel.conversation_state import ConversationState
from sabel.local_router import LocalRouter
from sabel.ollama_client import LocalToolCall, OllamaResponse


class LocalRouterTests(unittest.TestCase):
    def test_natural_phrasings_are_selected_by_mocked_ollama(self):
        examples = [
            ("Open YouTube", "open_website", {"url": "youtube.com"}),
            ("Go to SSundee’s YouTube channel", "open_youtube_search", {"query": "SSundee official channel"}),
            ("Open Visual Studio Code", "open_application", {"application_name": "Visual Studio Code"}),
            ("Clear my Trash", "empty_trash", {}),
            ("What can you do?", "show_capabilities", {}),
            ("Search Google for the best laptops", "open_web_search", {"query": "best laptops", "search_engine": "google"}),
            (
                "Research the five best laptops currently on the market",
                "delegate_to_openai",
                {"task": "Research the five best laptops currently on the market", "reason": "current multi-source comparison", "requires_current_web_information": True},
            ),
        ]
        for text, name, arguments in examples:
            with self.subTest(text=text):
                client = Mock()
                client.chat.return_value = OllamaResponse("", [LocalToolCall(name, arguments)], 1.0)
                decision = LocalRouter(client, ConversationState(6)).route(text)
                self.assertEqual(decision.tool_name, name)
                self.assertEqual(decision.arguments, arguments)

    def test_ambiguous_request_can_return_clarification(self):
        client = Mock()
        client.chat.return_value = OllamaResponse("Which application should I open?", [], 1.0)
        decision = LocalRouter(client, ConversationState(6)).route("Open it")
        self.assertIn("Which application", decision.message)

    def test_pending_action_uses_confirmation_tools_only(self):
        state = ConversationState(6)
        state.set_pending("empty_trash")
        client = Mock()
        client.chat.return_value = OllamaResponse("", [LocalToolCall("cancel_pending_action", {})], 1.0)
        LocalRouter(client, state).route("Never mind")
        tools = client.chat.call_args.args[1]
        names = [tool["function"]["name"] for tool in tools]
        self.assertEqual(names, ["confirm_pending_action", "cancel_pending_action"])


if __name__ == "__main__":
    unittest.main()

