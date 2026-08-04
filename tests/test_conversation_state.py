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
        state.set_pending(
            "empty_trash",
            {"original": "argument"},
            "description",
            "warning",
            created_at=100.0,
        )
        pending = state.pending_action
        self.assertEqual(pending.tool_name, "empty_trash")
        self.assertEqual(pending.arguments, {"original": "argument"})
        self.assertEqual(pending.description, "description")
        self.assertEqual(pending.warning, "warning")
        self.assertFalse(state.pending_expired(now=159.9))
        expired = state.clear_expired_pending(now=160.0)
        self.assertEqual(expired.tool_name, "empty_trash")
        self.assertIsNone(state.pending_action)


if __name__ == "__main__":
    unittest.main()
