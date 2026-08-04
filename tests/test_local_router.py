import unittest
from unittest.mock import Mock

from sabel.conversation_state import ConversationState
from sabel.confirmations import TRASH_DESCRIPTION, TRASH_WARNING
from sabel.local_router import LocalRouter, PendingActionRouter
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
        state.set_pending(
            "empty_trash", {}, TRASH_DESCRIPTION, TRASH_WARNING
        )
        client = Mock()
        client.chat.return_value = OllamaResponse("", [LocalToolCall("cancel_pending_action", {})], 1.0)
        PendingActionRouter(client, state).route("Never mind")
        tools = client.chat.call_args.args[1]
        names = [tool["function"]["name"] for tool in tools]
        self.assertEqual(
            names,
            [
                "confirm_pending_action",
                "cancel_pending_action",
                "explain_pending_action",
                "route_new_request",
            ],
        )
        for tool in tools:
            parameters = tool["function"]["parameters"]
            self.assertEqual(parameters["properties"], {})
            self.assertEqual(parameters["required"], [])

    def test_pending_router_receives_previous_assistant_response(self):
        state = ConversationState(10)
        state.add_exchange("Clear my Trash", TRASH_WARNING)
        state.set_pending(
            "empty_trash", {}, TRASH_DESCRIPTION, TRASH_WARNING
        )
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "", [LocalToolCall("explain_pending_action", {})], 1.0
        )

        PendingActionRouter(client, state).route("what does that mean")

        messages = client.chat.call_args.args[0]
        self.assertTrue(
            any(
                message["role"] == "assistant"
                and message["content"] == TRASH_WARNING
                for message in messages
            )
        )

    def test_pending_safety_reconciliation_handles_small_model_misses(self):
        examples = [
            (
                "what does that mean",
                OllamaResponse('{"name":"explain_pending_action","arguments":{}}', [], 1.0),
                "explain_pending_action",
            ),
            (
                "Open YouTube instead",
                OllamaResponse("Confirm pending action: Open YouTube instead", [], 1.0),
                "route_new_request",
            ),
            (
                "okay",
                OllamaResponse("", [LocalToolCall("confirm_pending_action", {})], 1.0),
                None,
            ),
        ]
        for text, response, expected_tool in examples:
            with self.subTest(text=text):
                state = ConversationState(10)
                state.set_pending(
                    "empty_trash", {}, TRASH_DESCRIPTION, TRASH_WARNING
                )
                client = Mock()
                client.chat.return_value = response
                decision = PendingActionRouter(client, state).route(text)
                self.assertEqual(decision.tool_name, expected_tool)
                if text == "okay":
                    self.assertIn("permanently", decision.message)


if __name__ == "__main__":
    unittest.main()
