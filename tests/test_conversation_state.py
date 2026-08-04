import unittest

from sabel.conversation_state import ConversationState


class ConversationStateTests(unittest.TestCase):
    def test_history_is_bounded(self):
        state = ConversationState(4)
        state.add_exchange("one", "answer one")
        state.add_exchange("two", "answer two")
        state.add_exchange("three", "answer three")
        self.assertEqual(len(state.messages()), 4)
        self.assertEqual(state.messages()[0]["content"], "two")

    def test_new_research_replaces_old_sources(self):
        state = ConversationState(4)
        state.store_research("old", ["https://old.example"])
        state.store_research("new", ["https://new.example"])
        self.assertEqual(state.research_sources, ["https://new.example"])

    def test_pending_action_is_structured_and_expires(self):
        state = ConversationState(10, pending_action_ttl=60)
        state.set_pending_destructive_action(
            "empty_trash",
            {"original": "argument"},
            "description",
            "warning",
            created_at=100.0,
        )
        pending = state.pending_destructive_action
        self.assertEqual(pending.tool_name, "empty_trash")
        self.assertEqual(pending.arguments, {"original": "argument"})
        self.assertEqual(pending.description, "description")
        self.assertEqual(pending.warning, "warning")
        self.assertFalse(state.pending_expired(now=159.9))
        expired = state.clear_expired_pending_destructive_action(now=160.0)
        self.assertEqual(expired.tool_name, "empty_trash")
        self.assertIsNone(state.pending_destructive_action)

    def test_clarification_state_is_separate_and_expires(self):
        state = ConversationState(10, clarification_ttl=60)
        state.set_pending_clarification(
            "play Circles",
            "Do you mean Spotify?",
            created_at=100.0,
            intent="open_spotify_search",
            collected_slots={"query": "Circles"},
            missing_slots=["service"],
            proposed_slots={"service": "spotify"},
        )
        self.assertIsNone(state.pending_destructive_action)
        self.assertEqual(state.pending_clarification.original_request, "play Circles")
        self.assertEqual(state.pending_clarification.intent, "open_spotify_search")
        self.assertEqual(
            state.pending_clarification.collected_slots, {"query": "Circles"}
        )
        self.assertEqual(state.pending_clarification.missing_slots, ["service"])
        self.assertFalse(state.clarification_expired(now=159.0))
        expired = state.clear_expired_clarification(now=160.0)
        self.assertEqual(expired.question, "Do you mean Spotify?")
        self.assertIsNone(state.pending_clarification)

    def test_history_and_last_action_result_are_distinct(self):
        state = ConversationState(4)
        state.add_exchange("hello", "Hey")
        state.record_action_result("open_website", True, "Opening YouTube")
        self.assertEqual(state.messages()[-1]["content"], "Hey")
        self.assertEqual(state.last_action_result.tool_name, "open_website")
        self.assertIn("succeeded", state.action_context())

    def test_validated_browser_context_supports_safe_followups(self):
        state = ConversationState(10)
        state.record_browser_context("youtube", "personal", "cookies")
        self.assertEqual(state.last_browser_service, "youtube")
        self.assertEqual(state.last_browser_profile, "personal")
        self.assertEqual(state.last_browser_query, "cookies")
        state.record_browser_context("google", None)
        self.assertEqual(state.last_browser_service, "google")
        self.assertIsNone(state.last_browser_profile)
        self.assertEqual(state.last_browser_query, "cookies")

    def test_cloud_fallback_is_structured_and_expires(self):
        state = ConversationState(10, clarification_ttl=60)
        state.set_pending_cloud_fallback(
            "Research current laptops",
            "Research current laptops",
            True,
            created_at=100.0,
        )
        pending = state.pending_cloud_fallback
        self.assertEqual(pending.task, "Research current laptops")
        self.assertTrue(pending.requires_current_web_information)
        self.assertFalse(state.cloud_fallback_expired(now=159.9))
        self.assertIsNotNone(state.clear_expired_cloud_fallback(now=160.0))
        self.assertIsNone(state.pending_cloud_fallback)


if __name__ == "__main__":
    unittest.main()
