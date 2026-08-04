from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sabel.config import Settings
from sabel.errors import CloudResearchError
from sabel.openai_research import OpenAIResearchService


class OpenAIResearchTests(unittest.TestCase):
    def test_uses_web_search_and_extracts_sources(self):
        source = SimpleNamespace(url="https://example.com/source")
        output = [SimpleNamespace(type="web_search_call", action=SimpleNamespace(sources=[source]))]
        response = SimpleNamespace(output_text="researched answer", output=output, usage={"total_tokens": 10})
        client = Mock()
        client.responses.create.return_value = response
        service = OpenAIResearchService(Settings(openai_api_key="test"), client=client)

        result = service.research("Research laptops")

        self.assertEqual(result.sources, ["https://example.com/source"])
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["tools"], [{"type": "web_search"}])
        self.assertNotIn("open_application", str(kwargs))

    def test_quota_rate_network_and_malformed_fail_cleanly(self):
        for error_name in ("RateLimitError", "APIConnectionError"):
            with self.subTest(error=error_name):
                client = Mock()
                client.responses.create.side_effect = type(error_name, (Exception,), {})()
                with self.assertRaises(CloudResearchError):
                    OpenAIResearchService(Settings(openai_api_key="test"), client=client).research("task")
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output_text="", output=[])
        with self.assertRaises(CloudResearchError):
            OpenAIResearchService(Settings(openai_api_key="test"), client=client).research("task")

    def test_missing_key_makes_no_request(self):
        client = Mock()
        with self.assertRaises(CloudResearchError):
            OpenAIResearchService(Settings(), client=client).research("task")
        client.responses.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()

