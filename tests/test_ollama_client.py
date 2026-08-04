from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sabel.config import Settings
from sabel.ollama_client import OllamaClient


class OllamaClientTests(unittest.TestCase):
    def test_uses_native_tools_and_disables_thinking(self):
        call = SimpleNamespace(
            function=SimpleNamespace(name="open_application", arguments={"application_name": "Notes"})
        )
        sdk = Mock()
        sdk.chat.return_value = SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[call])
        )
        client = OllamaClient(Settings(), client=sdk)

        response = client.chat([{"role": "user", "content": "Start Notes"}], [{"type": "function"}])

        self.assertEqual(response.tool_calls[0].name, "open_application")
        kwargs = sdk.chat.call_args.kwargs
        self.assertFalse(kwargs["think"])
        self.assertFalse(kwargs["stream"])
        self.assertEqual(kwargs["keep_alive"], "1m")
        self.assertIn("tools", kwargs)

    def test_availability_uses_local_list_only(self):
        sdk = Mock()
        self.assertTrue(OllamaClient(Settings(), client=sdk).is_available())
        sdk.list.assert_called_once()


if __name__ == "__main__":
    unittest.main()

