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


if __name__ == "__main__":
    unittest.main()
