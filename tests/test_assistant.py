import unittest
from unittest.mock import Mock

from sabel.actions import ActionResult
from sabel.browser_copilot import BrowserOutcome
from sabel.browser_models import BrowserActionState
from sabel.assistant import AssistantResultType, SabelAssistant
from sabel.cloud_router import CloudResult, CloudRouter
from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.local_router import (
    ClarificationRequest,
    ClarificationReply,
    ClarificationReplyType,
    RouterResult,
    RouterResultType,
)
from sabel.ollama_client import LocalToolCall
from sabel.tool_dispatcher import ToolDispatcher


def response(message: str) -> RouterResult:
    return RouterResult(RouterResultType.RESPONSE, message=message, duration_ms=1.0)


def tool(name: str, arguments=None, cloud=False) -> RouterResult:
    return RouterResult(
        RouterResultType.CLOUD_DELEGATION if cloud else RouterResultType.TOOL_CALLS,
        tool_calls=[LocalToolCall(name, arguments or {})],
        duration_ms=1.0,
    )


def clarification(question: str, slot="target") -> RouterResult:
    return RouterResult(
        RouterResultType.CLARIFICATION,
        message=question,
        expected_slot=slot,
        duration_ms=1.0,
    )


class AssistantIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(cloud_mode="ask", openai_api_key="test")
        self.state = ConversationState(10)
        self.handlers = {
            "open_service": Mock(return_value=ActionResult(True, "Opening YouTube.")),
            "open_website": Mock(return_value=ActionResult(True, "Opening YouTube.")),
            "open_application": Mock(return_value=ActionResult(True, "Opening Notes.")),
            "open_spotify_search": Mock(
                return_value=ActionResult(True, "Opening Spotify results for “Circles.”")
            ),
            "open_youtube_search": Mock(return_value=ActionResult(True, "Searching YouTube.")),
            "open_web_search": Mock(return_value=ActionResult(True, "Searching the web.")),
            "empty_trash": Mock(return_value=ActionResult(True, "Trash emptied successfully.")),
            "get_trash_status": Mock(
                return_value=ActionResult(True, "Your Trash contains 3 items.")
            ),
        }
        self.dispatcher = ToolDispatcher(
            self.settings, self.state, lambda: True, self.handlers
        )
        self.ollama = Mock()
        self.local = Mock()
        self.pending = Mock()
        self.clarifications = Mock()
        self.cloud = Mock()

    def assistant(self):
        return SabelAssistant(
            self.settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=self.local,
            pending_router=self.pending,
            clarification_router=self.clarifications,
            dispatcher=self.dispatcher,
            cloud_router=self.cloud,
        )

    def test_casual_conversation_is_visible_without_tool_dispatch(self):
        examples = [
            ("hello", "Hey. What do you need?"),
            ("hey, how's your day?", "Doing well. What are we working on?"),
        ]
        assistant = self.assistant()
        self.local.route.side_effect = [response(answer) for _, answer in examples]
        for text, answer in examples:
            with self.subTest(text=text):
                result = assistant.handle(text)
                self.assertEqual(result.result_type, AssistantResultType.RESPONSE)
                self.assertEqual(result.message, answer)
        for handler in self.handlers.values():
            handler.assert_not_called()

    def test_stop_action_cancels_clarification_and_it_does_not_repeat(self):
        self.local.route.side_effect = [
            clarification("Do you mean Spotify?", "application"),
            response("Hey. What do you need?"),
        ]
        assistant = self.assistant()

        first = assistant.handle("play Circles")
        cancelled = assistant.handle("stop action")
        later = assistant.handle("hello")

        self.assertEqual(first.result_type, AssistantResultType.CLARIFICATION)
        self.assertEqual(cancelled.message, "Cancelled.")
        self.assertEqual(later.message, "Hey. What do you need?")
        self.assertIsNone(self.state.pending_clarification)
        self.clarifications.route.assert_not_called()
        self.assertEqual(self.local.route.call_count, 2)

    def test_never_mind_cancels_clarification(self):
        self.state.set_pending_clarification(
            "play Circles", "Do you mean Spotify?", "application"
        )
        result = self.assistant().handle("never mind")
        self.assertEqual(result.message, "Cancelled.")
        self.assertIsNone(self.state.pending_clarification)

    def test_unrelated_request_clears_clarification_then_routes_normally(self):
        self.state.set_pending_clarification(
            "play Circles", "Do you mean Spotify?", "application"
        )
        self.clarifications.route.return_value = ClarificationReply(
            ClarificationReplyType.NEW_REQUEST, duration_ms=1.0
        )
        self.local.route.return_value = tool("open_website", {"url": "youtube.com"})

        result = self.assistant().handle("Open YouTube instead")

        self.assertEqual(
            result.message,
            "Cancelled the previous request. Opening YouTube.",
        )
        self.assertIsNone(self.state.pending_clarification)
        self.handlers["open_website"].assert_called_once_with("youtube.com")

    def test_clarification_answer_clears_state_and_combines_original_request(self):
        self.state.set_pending_clarification(
            "open an application", "Which application?", "application_name"
        )
        self.clarifications.route.return_value = ClarificationReply(
            ClarificationReplyType.ANSWER, duration_ms=1.0
        )
        self.local.route.return_value = tool(
            "open_application", {"application_name": "Notes"}
        )

        result = self.assistant().handle("Notes")

        routing_text = self.local.route.call_args.args[0]
        self.assertIn("Original request: open an application", routing_text)
        self.assertIn("Clarification answer: Notes", routing_text)
        self.assertEqual(result.message, "Opening Notes.")
        self.assertIsNone(self.state.pending_clarification)

    def test_question_about_clarification_keeps_it_pending(self):
        self.state.set_pending_clarification(
            "play Circles", "Do you mean Spotify?", "play_action"
        )
        self.clarifications.route.return_value = ClarificationReply(
            ClarificationReplyType.QUESTION, duration_ms=1.0
        )
        result = self.assistant().handle("what do you mean?")
        self.assertEqual(result.message, "I was asking: Do you mean Spotify?")
        self.assertIsNotNone(self.state.pending_clarification)
        self.assertEqual(
            self.state.messages()[-1]["content"],
            "I was asking: Do you mean Spotify?",
        )

    def test_clear_trash_creates_destructive_state_without_printing_tool_name(self):
        self.local.route.return_value = tool("empty_trash")
        result = self.assistant().handle("clear my trash")
        self.assertIn("permanently delete", result.message)
        self.assertNotIn("empty_trash", result.message)
        self.assertIsNotNone(self.state.pending_destructive_action)
        self.handlers["empty_trash"].assert_not_called()
        self.assertNotIn("empty_trash", self.state.messages()[-1]["content"])

    def test_pending_confirmation_executes_stored_action(self):
        self.state.set_pending_destructive_action(
            "empty_trash", {}, "description", "warning"
        )
        self.pending.route.return_value = tool("confirm_pending_action")
        result = self.assistant().handle("Yes, empty the Trash")
        self.assertEqual(result.message, "Trash emptied successfully.")
        self.handlers["empty_trash"].assert_called_once_with()
        self.assertIsNone(self.state.pending_destructive_action)
        self.local.route.assert_not_called()

    def test_trash_status_and_sabel_status_are_distinct(self):
        self.local.route.side_effect = [
            tool("get_trash_status"),
            tool("show_status"),
        ]
        trash = self.assistant().handle("is my trash empty?")
        self.assertEqual(trash.message, "Your Trash contains 3 items.")
        self.assertNotIn("SABEL status", trash.message)

        runtime = self.assistant().handle("status")
        self.assertIn("SABEL status", runtime.message)
        self.assertEqual(self.local.route.call_count, 2)

    def test_show_your_status_is_deterministic_application_state(self):
        self.local.route.return_value = tool("show_status")
        result = self.assistant().handle("show your status")
        self.assertEqual(
            result.message,
            "SABEL status\n"
            "Local model: qwen3:1.7b\n"
            "Ollama: Available\n"
            "Cloud mode: Ask\n"
            "OpenAI configured: Yes\n"
            "Pending destructive action: None\n"
            "Pending clarification: None",
        )

    def test_raw_internal_name_from_router_is_never_rendered_or_stored(self):
        self.local.route.return_value = RouterResult(
            RouterResultType.ERROR,
            message="I could not safely interpret that request.",
            duration_ms=1.0,
        )
        result = self.assistant().handle("clear my trash")
        self.assertNotEqual(result.message, "empty_trash")
        self.assertNotIn("empty_trash", self.state.messages()[-1]["content"])

    def test_raw_tool_prose_and_application_path_are_never_visible_or_stored(self):
        self.local.route.return_value = response(
            "open_application /Applications/Safari.app"
        )
        result = self.assistant().handle("open safari")
        self.assertNotIn("open_application", result.message)
        self.assertNotIn("/Applications", result.message)
        self.assertEqual(self.state.messages()[-1]["content"], result.message)

    def test_typed_application_call_executes_handler_with_name_and_friendly_result(self):
        self.handlers["open_application"].return_value = ActionResult(
            True, "Opening Safari."
        )
        self.local.route.return_value = tool(
            "open_application", {"application_name": "/Applications/Safari.app"}
        )
        result = self.assistant().handle("open safari")
        self.assertEqual(result.message, "Opening Safari.")
        self.handlers["open_application"].assert_called_once_with("Safari")
        self.assertNotIn("/Applications", self.state.messages()[-1]["content"])

    def test_go_to_youtube_executes_registered_service(self):
        self.local.route.return_value = tool(
            "open_service", {"service_name": "youtube"}
        )
        result = self.assistant().handle("go to youtube")
        self.assertEqual(result.message, "Opening YouTube.")
        self.handlers["open_service"].assert_called_once_with("youtube", "")

    def test_clarification_expires_and_normal_command_still_works(self):
        self.state.set_pending_clarification(
            "old request", "Old question?", created_at=0.0
        )
        self.local.route.return_value = response("Hey. What do you need?")
        result = self.assistant().handle("hello")
        self.assertIn("previous clarification expired", result.message)
        self.assertIn("Hey. What do you need?", result.message)
        self.assertIsNone(self.state.pending_clarification)
        self.clarifications.route.assert_not_called()

    def test_local_action_never_calls_cloud(self):
        self.local.route.return_value = tool("open_website", {"url": "youtube.com"})
        result = self.assistant().handle("Open YouTube")
        self.assertEqual(result.message, "Opening YouTube.")
        self.cloud.handle.assert_not_called()

    def test_cloud_off_intercepts_delegation_without_initializing_openai(self):
        settings = Settings(cloud_mode="off")
        factory = Mock()
        cloud_router = CloudRouter(settings, self.state, factory)
        dispatcher = ToolDispatcher(settings, self.state, lambda: True, self.handlers)
        self.local.route.return_value = tool(
            "delegate_to_openai",
            {
                "task": "five best laptops on the market",
                "reason": "current research",
                "requires_current_web_information": True,
            },
            cloud=True,
        )
        assistant = SabelAssistant(
            settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=self.local,
            pending_router=self.pending,
            clarification_router=self.clarifications,
            dispatcher=dispatcher,
            cloud_router=cloud_router,
        )

        result = assistant.handle("research the five best laptops right now")

        self.assertIn("cloud mode is disabled", result.message)
        self.assertIn("Open a browser search", result.message)
        self.assertNotIn("delegate_to_openai", result.message)
        factory.assert_not_called()

    def test_cloud_off_option_two_is_immediate_labeled_and_not_current(self):
        settings = Settings(cloud_mode="off")
        factory = Mock()
        cloud_router = CloudRouter(settings, self.state, factory)
        dispatcher = ToolDispatcher(settings, self.state, lambda: True, self.handlers)
        self.local.route.return_value = tool(
            "delegate_to_openai",
            {
                "task": "Research the five best laptops on the market right now",
                "reason": "current research",
                "requires_current_web_information": True,
            },
            cloud=True,
        )
        assistant = SabelAssistant(
            settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=self.local,
            pending_router=self.pending,
            clarification_router=self.clarifications,
            dispatcher=dispatcher,
            cloud_router=cloud_router,
        )

        offered = assistant.handle("research the five best laptops right now")
        guidance = assistant.handle("2")
        dated = assistant.handle("Around what date are these recommendations valid?")

        self.assertIn("cloud mode is disabled", offered.message)
        self.assertIn(
            "Local fallback — current web information was not verified",
            guidance.message,
        )
        self.assertNotIn("2025", guidance.message)
        self.assertNotIn("2026", guidance.message)
        self.assertIn("cannot assign a verified current date", dated.message)
        self.assertEqual(self.local.route.call_count, 1)
        factory.assert_not_called()

    def test_cloud_off_option_one_opens_search_without_openai(self):
        settings = Settings(cloud_mode="off")
        factory = Mock()
        cloud_router = CloudRouter(settings, self.state, factory)
        dispatcher = ToolDispatcher(settings, self.state, lambda: True, self.handlers)
        self.local.route.return_value = tool(
            "delegate_to_openai",
            {
                "task": "Research current laptops",
                "reason": "current research",
                "requires_current_web_information": True,
            },
            cloud=True,
        )
        assistant = SabelAssistant(
            settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=self.local,
            pending_router=self.pending,
            clarification_router=self.clarifications,
            dispatcher=dispatcher,
            cloud_router=cloud_router,
        )

        assistant.handle("research current laptops")
        opened = assistant.handle("1")

        self.assertEqual(opened.message, "Searching the web.")
        self.handlers["open_web_search"].assert_called_once_with(
            "Research current laptops", "google"
        )
        factory.assert_not_called()

    def test_spotify_confirmation_yes_uses_stored_interpretation(self):
        self.state.set_pending_clarification(
            "play circles",
            "Do you mean “Circles” on Spotify?",
            intent="open_spotify_search",
            collected_slots={"query": "circles"},
            missing_slots=["service"],
            proposed_slots={"service": "spotify"},
        )
        self.clarifications.route.return_value = ClarificationReply(
            ClarificationReplyType.CONFIRMS_SUGGESTION
        )
        result = self.assistant().handle("yes")
        self.assertEqual(result.message, "Opening Spotify results for “Circles.”")
        self.handlers["open_spotify_search"].assert_called_once_with("circles")
        self.assertIsNone(self.state.pending_clarification)

    def test_spotify_detailed_confirmation_resolves_without_rerouting(self):
        self.state.set_pending_clarification(
            "play circles",
            "Do you mean “Circles” on Spotify?",
            intent="open_spotify_search",
            collected_slots={"query": "circles"},
            missing_slots=["service"],
            proposed_slots={"service": "spotify"},
        )
        self.clarifications.route.return_value = ClarificationReply(
            ClarificationReplyType.CONFIRMS_SUGGESTION,
            supplied_slots={"service": "spotify", "query": "circles"},
        )
        result = self.assistant().handle("yes, circles on spotify")
        self.assertEqual(result.message, "Opening Spotify results for “Circles.”")
        self.local.route.assert_not_called()

    def test_spotify_correction_updates_intent_to_youtube_search(self):
        self.state.set_pending_clarification(
            "play circles",
            "Do you mean “Circles” on Spotify?",
            intent="open_spotify_search",
            collected_slots={"query": "circles"},
            missing_slots=["service"],
            proposed_slots={"service": "spotify"},
        )
        self.clarifications.route.return_value = ClarificationReply(
            ClarificationReplyType.REJECTS_SUGGESTION,
            supplied_slots={"service": "youtube"},
            replacement_intent="open_youtube_search",
        )
        result = self.assistant().handle("no, use YouTube")
        self.assertEqual(result.message, "Searching YouTube.")
        self.handlers["open_youtube_search"].assert_called_once_with("circles")
        self.assertIsNone(self.state.pending_clarification)

    def test_cloud_failure_does_not_disable_next_local_command(self):
        self.local.route.side_effect = [
            tool(
                "delegate_to_openai",
                {
                    "task": "Research laptops",
                    "reason": "current",
                    "requires_current_web_information": True,
                },
                cloud=True,
            ),
            tool("open_application", {"application_name": "Notes"}),
        ]
        self.cloud.handle.return_value = CloudResult(
            "Cloud research unavailable; local commands still work"
        )
        assistant = self.assistant()
        first = assistant.handle("Research laptops", lambda prompt: "yes")
        second = assistant.handle("Open Notes")
        self.assertIn("unavailable", first.message)
        self.assertEqual(second.message, "Opening Notes.")

    def test_browser_gmail_clarification_preserves_service_and_profile_reply(self):
        browser = Mock()
        browser.open_service_in_profile.side_effect = [
            BrowserOutcome(
                BrowserActionState.PLANNED,
                "Which Gmail account should I use: Personal or NYU?",
                False,
                clarification="Which Gmail account should I use: Personal or NYU?",
            ),
            BrowserOutcome(
                BrowserActionState.EXECUTED,
                "Opening Personal Gmail in your Personal Chrome profile.",
                True,
            ),
        ]
        dispatcher = ToolDispatcher(
            self.settings,
            self.state,
            lambda: True,
            self.handlers,
            browser,
        )
        self.local.route.return_value = tool(
            "open_service_in_profile", {"service": "gmail"}
        )
        self.clarifications.route.return_value = ClarificationReply(
            ClarificationReplyType.SUPPLIES_MISSING_INFORMATION,
            supplied_slots={"profile": "personal"},
        )
        assistant = SabelAssistant(
            self.settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=self.local,
            pending_router=self.pending,
            clarification_router=self.clarifications,
            dispatcher=dispatcher,
            cloud_router=self.cloud,
        )

        question = assistant.handle("Open Gmail")
        opened = assistant.handle("Personal")

        self.assertEqual(question.result_type, AssistantResultType.CLARIFICATION)
        self.assertEqual(
            opened.message,
            "Opening Personal Gmail in your Personal Chrome profile.",
        )
        browser.open_service_in_profile.assert_has_calls(
            [unittest.mock.call("gmail", None), unittest.mock.call("gmail", "personal")]
        )
        self.assertIsNone(self.state.pending_clarification)

    def test_successful_profile_search_records_context_for_followups(self):
        browser = Mock()
        browser.browser_search.return_value = BrowserOutcome(
            BrowserActionState.EXECUTED,
            "Searching Google for “dancing.”",
            True,
        )
        dispatcher = ToolDispatcher(
            self.settings, self.state, lambda: True, self.handlers, browser
        )
        self.local.route.return_value = tool(
            "browser_search",
            {"service": "google", "query": "dancing", "profile": "personal"},
        )
        assistant = SabelAssistant(
            self.settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=self.local,
            pending_router=self.pending,
            clarification_router=self.clarifications,
            dispatcher=dispatcher,
            cloud_router=self.cloud,
        )
        result = assistant.handle("search dancing in Google in Personal")
        self.assertEqual(result.message, "Searching Google for “dancing.”")
        self.assertEqual(self.state.last_browser_service, "google")
        self.assertEqual(self.state.last_browser_profile, "personal")
        self.assertEqual(self.state.last_browser_query, "dancing")

    def test_browser_search_clarification_executes_with_stored_query(self):
        browser = Mock()
        browser.browser_search.return_value = BrowserOutcome(
            BrowserActionState.EXECUTED,
            "Searching YouTube for “dancing.”",
            True,
        )
        dispatcher = ToolDispatcher(
            self.settings, self.state, lambda: True, self.handlers, browser
        )
        self.local.route.return_value = RouterResult(
            RouterResultType.CLARIFICATION,
            message="Should I search Google or YouTube for “dancing”?",
            clarification=ClarificationRequest(
                question="Should I search Google or YouTube for “dancing”?",
                intent="browser_search",
                collected_slots={"query": "dancing", "profile": "personal"},
                missing_slots=["service"],
            ),
        )
        self.clarifications.route.return_value = ClarificationReply(
            ClarificationReplyType.SUPPLIES_MISSING_INFORMATION,
            supplied_slots={"service": "youtube"},
        )
        assistant = SabelAssistant(
            self.settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=self.local,
            pending_router=self.pending,
            clarification_router=self.clarifications,
            dispatcher=dispatcher,
            cloud_router=self.cloud,
        )
        assistant.handle("search dancing in Personal")
        result = assistant.handle("YouTube")
        self.assertEqual(result.message, "Searching YouTube for “dancing.”")
        browser.browser_search.assert_called_once_with(
            "youtube", "dancing", "personal"
        )

    def test_verified_browser_outcome_is_rendered_without_internal_values(self):
        browser = Mock()
        browser.browser_copilot_task.return_value = BrowserOutcome(
            BrowserActionState.VERIFIED,
            "Opened Taz Skylar’s YouTube channel.",
            True,
            True,
        )
        dispatcher = ToolDispatcher(
            self.settings, self.state, lambda: True, self.handlers, browser
        )
        self.local.route.return_value = tool(
            "browser_copilot_task",
            {
                "objective": "Go to Taz Skylar's YouTube channel",
                "service": "youtube",
                "profile": "personal",
            },
        )
        assistant = SabelAssistant(
            self.settings,
            ollama_client=self.ollama,
            state=self.state,
            local_router=self.local,
            pending_router=self.pending,
            clarification_router=self.clarifications,
            dispatcher=dispatcher,
            cloud_router=self.cloud,
        )
        result = assistant.handle("Go to Taz Skylar's YouTube channel")
        self.assertEqual(result.message, "Opened Taz Skylar’s YouTube channel.")
        self.assertNotIn("browser_copilot_task", result.message)
        self.assertEqual(self.state.messages()[-1]["content"], result.message)


if __name__ == "__main__":
    unittest.main()
