import unittest
from unittest.mock import Mock

from sabel.cloud_router import CloudRouter
from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.errors import CloudResearchError
from sabel.openai_research import ResearchResult
from sabel.tool_dispatcher import DelegationRequest


class CloudRouterTests(unittest.TestCase):
    def setUp(self):
        self.request = DelegationRequest("Research laptops", "current comparison", True)
        self.state = ConversationState(6)

    def test_off_never_initializes_openai(self):
        factory = Mock()
        result = CloudRouter(Settings(cloud_mode="off"), self.state, factory).handle(self.request)
        self.assertIn("cloud mode is disabled", result.message)
        self.assertIn("Open a browser search", result.message)
        self.assertIn("general guidance", result.message)
        self.assertIsNotNone(self.state.pending_cloud_fallback)
        factory.assert_not_called()

    def test_ask_requires_explicit_approval(self):
        factory = Mock()
        router = CloudRouter(Settings(cloud_mode="ask", openai_api_key="test"), self.state, factory)
        result = router.handle(self.request, lambda prompt: "no")
        self.assertIn("cancelled", result.message)
        factory.assert_not_called()

    def test_ask_and_auto_can_research(self):
        for mode, callback in (("ask", lambda prompt: "yes"), ("auto", None)):
            with self.subTest(mode=mode):
                service = Mock()
                service.research.return_value = ResearchResult("answer", ["https://example.com"], 2.0)
                router = CloudRouter(Settings(cloud_mode=mode, openai_api_key="test"), ConversationState(6), lambda: service)
                result = router.handle(self.request, callback)
                self.assertTrue(result.used_cloud)
                self.assertIn("https://example.com", result.message)

    def test_missing_key_and_cloud_failure_are_readable(self):
        factory = Mock()
        missing = CloudRouter(Settings(cloud_mode="auto"), self.state, factory).handle(self.request)
        self.assertIn("OPENAI_API_KEY", missing.message)
        factory.assert_not_called()

        service = Mock()
        service.research.side_effect = CloudResearchError("quota failure; local commands still work")
        failed = CloudRouter(Settings(cloud_mode="auto", openai_api_key="test"), self.state, lambda: service).handle(self.request)
        self.assertIn("local commands", failed.message)


if __name__ == "__main__":
    unittest.main()
