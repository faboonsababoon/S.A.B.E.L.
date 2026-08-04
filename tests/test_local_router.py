import unittest
from unittest.mock import Mock

from sabel.confirmations import TRASH_DESCRIPTION, TRASH_WARNING
from sabel.conversation_state import ConversationState
from sabel.local_router import (
    ClarificationReplyType,
    ClarificationRouter,
    LocalRouter,
    PendingActionRouter,
    RouterResult,
    RouterResultType,
)
from sabel.ollama_client import LocalToolCall, OllamaResponse


class LocalRouterTests(unittest.TestCase):
    def test_router_result_types_are_explicit(self):
        self.assertEqual(RouterResultType.RESPONSE.value, "response")
        self.assertEqual(RouterResultType.TOOL_CALLS.value, "tool_calls")
        self.assertTrue(hasattr(RouterResult, "__dataclass_fields__"))

    def test_natural_phrasings_are_typed_tool_calls(self):
        examples = [
            (
                "Open YouTube",
                "open_service_in_profile",
                {"service": "youtube", "profile": "personal"},
            ),
            (
                "Go to SSundee’s YouTube channel",
                "browser_copilot_task",
                {
                    "objective": "Go to SSundee’s YouTube channel",
                    "service": "youtube",
                    "profile": "personal",
                },
            ),
            ("Open Visual Studio Code", "open_application", {"application_name": "Visual Studio Code"}),
            ("Clear my Trash", "empty_trash", {}),
            ("What can you do?", "show_capabilities", {}),
            ("Is my Trash empty?", "get_trash_status", {}),
            (
                "Search Google for laptops",
                "browser_search",
                {"service": "google", "query": "laptops"},
            ),
            ("Show your status", "show_status", {}),
        ]
        for text, name, arguments in examples:
            with self.subTest(text=text):
                client = Mock()
                client.chat.return_value = OllamaResponse(
                    "", [LocalToolCall(name, arguments)], 1.0
                )
                result = LocalRouter(client, ConversationState(10)).route(text)
                self.assertEqual(result.result_type, RouterResultType.TOOL_CALLS)
                self.assertEqual(result.tool_calls, [LocalToolCall(name, arguments)])

    def test_natural_search_word_order_executes_instead_of_claiming_success(self):
        state = ConversationState(10)
        state.record_browser_context("youtube", "personal")
        client = Mock()
        client.chat.return_value = OllamaResponse(
            'Searching without a tool.', [], 1.0
        )
        router = LocalRouter(client, state)

        contextual = router.route(
            'search up "how to bake cookies" in personal'
        )
        self.assertEqual(
            contextual.tool_calls,
            [
                LocalToolCall(
                    "browser_search",
                    {
                        "service": "youtube",
                        "query": "how to bake cookies",
                        "profile": "personal",
                    },
                )
            ],
        )

        state.record_browser_context(
            "google", "personal", "how to bake cookies"
        )
        same_query = router.route("search that same string up in google.com")
        in_it = router.route("search up baking cookies in it")
        youtube = router.route("search ssundee in youtube")
        self.assertEqual(
            same_query.tool_calls[0].arguments,
            {
                "service": "google",
                "query": "how to bake cookies",
                "profile": "personal",
            },
        )
        self.assertEqual(
            in_it.tool_calls[0].arguments,
            {
                "service": "google",
                "query": "baking cookies",
                "profile": "personal",
            },
        )
        self.assertEqual(
            youtube.tool_calls[0].arguments,
            {
                "service": "youtube",
                "query": "ssundee",
                "profile": "personal",
            },
        )

    def test_search_without_service_or_context_asks_instead_of_faking_action(self):
        client = Mock()
        client.chat.return_value = OllamaResponse('Searching Google for "dancing"', [], 1.0)
        result = LocalRouter(client, ConversationState(10)).route(
            "search up dancing"
        )
        self.assertEqual(result.result_type, RouterResultType.CLARIFICATION)
        self.assertEqual(result.clarification.intent, "browser_search")
        self.assertEqual(result.clarification.collected_slots, {"query": "dancing"})
        self.assertEqual(result.clarification.missing_slots, ["service"])
        self.assertNotEqual(result.message, 'Searching Google for "dancing"')

    def test_cloud_delegation_is_explicit(self):
        client = Mock()
        call = LocalToolCall(
            "delegate_to_openai",
            {
                "task": "Research laptops",
                "reason": "current comparison",
                "requires_current_web_information": True,
            },
        )
        client.chat.return_value = OllamaResponse("", [call], 1.0)
        result = LocalRouter(client, ConversationState(10)).route("Research laptops")
        self.assertEqual(result.result_type, RouterResultType.CLOUD_DELEGATION)
        self.assertEqual(result.tool_calls, [call])

    def test_casual_conversation_is_a_normal_response(self):
        for text, answer in (
            ("hello", "Hey. What do you need?"),
            ("how's your day?", "Doing well. What are we working on?"),
        ):
            with self.subTest(text=text):
                client = Mock()
                client.chat.return_value = OllamaResponse(answer, [], 1.0)
                result = LocalRouter(client, ConversationState(10)).route(text)
                self.assertEqual(result.result_type, RouterResultType.RESPONSE)
                self.assertEqual(result.message, answer)
                tools = client.chat.call_args.args[1]
                self.assertNotIn("confirm_pending_action", str(tools))

    def test_clarification_requires_native_typed_call(self):
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "",
            [
                LocalToolCall(
                    "request_clarification",
                    {
                        "question": "Which application should I open?",
                        "expected_slot": "application_name",
                    },
                )
            ],
            1.0,
        )
        result = LocalRouter(client, ConversationState(10)).route("Open it")
        self.assertEqual(result.result_type, RouterResultType.CLARIFICATION)
        self.assertEqual(result.message, "Which application should I open?")
        self.assertEqual(result.expected_slot, "application_name")

    def test_raw_internal_tool_name_is_never_conversational_text(self):
        for internal_name in (
            "empty_trash",
            "show_status",
            "open_application /Applications/Safari.app",
            'delegate_to_openai {"task": "research"}',
        ):
            with self.subTest(name=internal_name):
                client = Mock()
                client.chat.return_value = OllamaResponse(internal_name, [], 1.0)
                result = LocalRouter(client, ConversationState(10)).route("request")
                self.assertEqual(result.result_type, RouterResultType.ERROR)
                self.assertNotEqual(result.message, internal_name)

    def test_malformed_application_prose_recovers_to_typed_safe_name(self):
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "open_application /Applications/Safari.app", [], 1.0
        )
        result = LocalRouter(client, ConversationState(10)).route("open safari")
        self.assertEqual(result.result_type, RouterResultType.TOOL_CALLS)
        self.assertEqual(
            result.tool_calls,
            [LocalToolCall("open_application", {"application_name": "Safari"})],
        )

    def test_status_and_service_recover_from_malformed_model_prose(self):
        examples = [
            ("show your status", "show_status", {}),
            (
                "go to YouTube",
                "open_service_in_profile",
                {"service": "youtube", "profile": "personal"},
            ),
        ]
        for text, expected_name, expected_arguments in examples:
            with self.subTest(text=text):
                client = Mock()
                client.chat.return_value = OllamaResponse(
                    f"{expected_name} unsafe prose", [], 1.0
                )
                result = LocalRouter(client, ConversationState(10)).route(text)
                self.assertEqual(result.result_type, RouterResultType.TOOL_CALLS)
                self.assertEqual(result.tool_calls[0].name, expected_name)
                self.assertEqual(result.tool_calls[0].arguments, expected_arguments)

    def test_raw_cloud_delegation_becomes_typed_policy_request(self):
        client = Mock()
        client.chat.return_value = OllamaResponse(
            'delegate_to_openai {"reason":"research"}', [], 1.0
        )
        result = LocalRouter(client, ConversationState(10)).route(
            "research the five best laptops on the market right now"
        )
        self.assertEqual(result.result_type, RouterResultType.CLOUD_DELEGATION)
        self.assertEqual(result.tool_calls[0].name, "delegate_to_openai")
        self.assertIsNotNone(result.cloud_request)
        self.assertTrue(result.cloud_request.requires_current_web_information)

    def test_explicit_url_remains_website_tool(self):
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "", [LocalToolCall("open_website", {"url": "https://www.nyu.edu"})], 1.0
        )
        result = LocalRouter(client, ConversationState(10)).route(
            "go to https://www.nyu.edu"
        )
        self.assertEqual(result.tool_calls[0].name, "open_website")

        client.chat.return_value = OllamaResponse(
            "", [LocalToolCall("open_service_in_profile", {"service": "youtube"})], 1.0
        )
        youtube_url = LocalRouter(client, ConversationState(10)).route(
            "open youtube.com"
        )
        self.assertEqual(
            youtube_url.tool_calls,
            [LocalToolCall("open_website", {"url": "youtube.com"})],
        )

    def test_bare_service_navigation_corrects_search_tool_misroute(self):
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "",
            [LocalToolCall("open_youtube_search", {"query": "youtube"})],
            1.0,
        )
        result = LocalRouter(client, ConversationState(10)).route("go to YouTube")
        self.assertEqual(
            result.tool_calls,
            [
                LocalToolCall(
                    "open_service_in_profile",
                    {"service": "youtube", "profile": "personal"},
                )
            ],
        )

    def test_channel_navigation_becomes_verified_browser_task(self):
        client = Mock()
        expected = LocalToolCall(
            "browser_copilot_task",
            {
                "objective": "go to SSundee's YouTube channel",
                "service": "youtube",
                "profile": "personal",
            },
        )
        client.chat.return_value = OllamaResponse("", [expected], 1.0)
        result = LocalRouter(client, ConversationState(10)).route(
            "go to SSundee's YouTube channel"
        )
        self.assertEqual(result.tool_calls, [expected])

    def test_profile_services_and_browser_commands_are_high_level_tools(self):
        cases = [
            (
                "open Albert",
                "open_service_in_profile",
                {"service": "albert", "profile": "nyu"},
            ),
            (
                "open personal Gmail",
                "open_service_in_profile",
                {"service": "personal_gmail", "profile": "personal"},
            ),
            (
                "open NYU Gmail",
                "open_service_in_profile",
                {"service": "nyu_gmail", "profile": "nyu"},
            ),
            ("show browser profiles", "show_browser_profiles", {}),
            ("list chrome profiles", "show_browser_profiles", {}),
            ("stop browser control", "stop_browser_task", {}),
            ("show recent browser actions", "show_recent_browser_actions", {}),
        ]
        for text, name, arguments in cases:
            with self.subTest(text=text):
                client = Mock()
                client.chat.return_value = OllamaResponse("misrouted", [], 1.0)
                result = LocalRouter(client, ConversationState(10)).route(text)
                self.assertEqual(result.tool_calls, [LocalToolCall(name, arguments)])

    def test_research_cannot_be_downgraded_to_unpoliced_browser_search(self):
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "",
            [LocalToolCall("open_web_search", {"query": "best laptops"})],
            1.0,
        )
        result = LocalRouter(client, ConversationState(10)).route(
            "research the best laptops right now"
        )
        self.assertEqual(result.result_type, RouterResultType.CLOUD_DELEGATION)
        self.assertEqual(result.tool_calls[0].name, "delegate_to_openai")

    def test_malformed_trash_output_uses_original_request_safe_fallback(self):
        examples = [
            ("clear my trash", "empty_trash", "empty_trash"),
            (
                "is my trash empty?",
                '{"name":"get_trash_status","arguments":{}}',
                "get_trash_status",
            ),
        ]
        for text, malformed, expected_tool in examples:
            with self.subTest(text=text):
                client = Mock()
                client.chat.return_value = OllamaResponse(malformed, [], 1.0)
                result = LocalRouter(client, ConversationState(10)).route(text)
                self.assertEqual(result.result_type, RouterResultType.TOOL_CALLS)
                self.assertEqual(result.tool_calls[0].name, expected_tool)
                self.assertEqual(result.tool_calls[0].arguments, {})
                self.assertEqual(client.chat.call_count, 2)
                active_names = {
                    tool["function"]["name"]
                    for tool in client.chat.call_args.args[1]
                }
                self.assertEqual(
                    active_names,
                    {"empty_trash", "get_trash_status", "request_clarification"},
                )

    def test_ambiguous_play_response_becomes_typed_clarification(self):
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "I cannot play media.", [], 1.0
        )
        result = LocalRouter(client, ConversationState(10)).route("play Circles")
        self.assertEqual(result.result_type, RouterResultType.CLARIFICATION)
        self.assertIn("Circles", result.message)
        self.assertEqual(result.expected_slot, "service")
        self.assertEqual(result.clarification.intent, "media_search")
        self.assertEqual(result.clarification.collected_slots, {"query": "Circles"})
        self.assertEqual(result.clarification.missing_slots, ["service"])

    def test_explicit_media_service_is_removed_from_query(self):
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "Playing Circles on Spotify.", [], 1.0
        )
        result = LocalRouter(client, ConversationState(10)).route(
            "play Circles on Spotify"
        )
        self.assertEqual(result.result_type, RouterResultType.TOOL_CALLS)
        self.assertEqual(
            result.tool_calls,
            [LocalToolCall("open_spotify_search", {"query": "Circles"})],
        )
        self.assertNotIn("on Spotify", result.tool_calls[0].arguments["query"])

    def test_saved_media_default_avoids_redundant_question(self):
        client = Mock()
        client.chat.return_value = OllamaResponse("Playing Baby.", [], 1.0)
        result = LocalRouter(
            client, ConversationState(10), default_music_service="spotify"
        ).route("play Baby")
        self.assertEqual(
            result.tool_calls,
            [LocalToolCall("open_spotify_search", {"query": "Baby"})],
        )

    def test_playback_control_never_claims_unverified_playback(self):
        client = Mock()
        client.chat.return_value = OllamaResponse("Playing now.", [], 1.0)
        result = LocalRouter(client, ConversationState(10)).route("press play")
        self.assertEqual(result.result_type, RouterResultType.RESPONSE)
        self.assertIn("cannot verify playback", result.message)
        self.assertNotIn("Playing now", result.message)

    def test_pending_destructive_mode_uses_only_compact_tools_and_history(self):
        state = ConversationState(10)
        state.add_exchange("Clear my Trash", TRASH_WARNING)
        state.set_pending_destructive_action(
            "empty_trash", {}, TRASH_DESCRIPTION, TRASH_WARNING
        )
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "", [LocalToolCall("cancel_pending_action", {})], 1.0
        )
        result = PendingActionRouter(client, state).route("Never mind")
        self.assertEqual(result.result_type, RouterResultType.TOOL_CALLS)
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
        messages = client.chat.call_args.args[0]
        self.assertTrue(
            any(m["role"] == "assistant" and m["content"] == TRASH_WARNING for m in messages)
        )

    def test_pending_safety_reconciliation_handles_small_model_misses(self):
        examples = [
            ("what does that mean", OllamaResponse("fake json", [], 1.0), "explain_pending_action"),
            ("Open YouTube instead", OllamaResponse("wrong prose", [], 1.0), "route_new_request"),
            ("okay", OllamaResponse("", [LocalToolCall("confirm_pending_action", {})], 1.0), None),
        ]
        for text, response, expected_tool in examples:
            with self.subTest(text=text):
                state = ConversationState(10)
                state.set_pending_destructive_action(
                    "empty_trash", {}, TRASH_DESCRIPTION, TRASH_WARNING
                )
                client = Mock()
                client.chat.return_value = response
                result = PendingActionRouter(client, state).route(text)
                actual = result.tool_calls[0].name if result.tool_calls else None
                self.assertEqual(actual, expected_tool)

    def test_clarification_mode_has_only_four_zero_argument_tools(self):
        state = ConversationState(10)
        state.set_pending_clarification(
            "play Circles", "Do you mean Spotify?", "application"
        )
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "", [LocalToolCall("cancel_clarification", {})], 1.0
        )
        reply = ClarificationRouter(client, state).route("never mind")
        self.assertEqual(reply.reply_type, ClarificationReplyType.CANCELLATION)
        client.chat.assert_not_called()

    def test_pending_spotify_replies_use_structured_context(self):
        state = ConversationState(10)
        state.set_pending_clarification(
            "play circles",
            "Do you mean “Circles” on Spotify?",
            intent="open_spotify_search",
            collected_slots={"query": "circles"},
            missing_slots=["service"],
            proposed_slots={"service": "spotify"},
        )
        router = ClarificationRouter(Mock(), state)

        yes = router.route("yes")
        detailed = router.route("yes, circles on spotify")
        correction = router.route("no, use YouTube")

        self.assertEqual(yes.reply_type, ClarificationReplyType.CONFIRMS_SUGGESTION)
        self.assertEqual(detailed.reply_type, ClarificationReplyType.CONFIRMS_SUGGESTION)
        self.assertEqual(detailed.supplied_slots["service"], "spotify")
        self.assertEqual(correction.reply_type, ClarificationReplyType.REJECTS_SUGGESTION)
        self.assertEqual(correction.replacement_intent, "open_youtube_search")


if __name__ == "__main__":
    unittest.main()
