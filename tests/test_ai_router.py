"""Mocked tests for the Responses API function-calling lifecycle."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sabel.ai_router import TOOLS, route_request
from sabel.config import Settings
from sabel.tool_executor import ToolExecutionResult


def response(output=None, text=""):
    return SimpleNamespace(output=output or [], output_text=text)


def function_call(name, arguments, call_id="call_123"):
    return SimpleNamespace(
        type="function_call",
        name=name,
        arguments=arguments,
        call_id=call_id,
    )


def fake_client(*responses):
    client = Mock()
    client.responses.create.side_effect = responses
    return client


class AIRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(api_key="test-key", model="test-model")

    def test_natural_website_request_executes_one_tool(self) -> None:
        call = function_call("open_website", '{"url":"youtube.com"}')
        client = fake_client(response([call]), response(text="Opening YouTube."))
        executor = Mock(
            return_value=ToolExecutionResult(True, "open_website", "opened")
        )

        result = route_request(
            "Could you open YouTube for me?",
            settings=self.settings,
            client=client,
            tool_executor=executor,
        )

        self.assertEqual(result.message, "Opening YouTube.")
        executor.assert_called_once_with("open_website", '{"url":"youtube.com"}')
        self.assertEqual(client.responses.create.call_count, 2)

    def test_natural_application_request_executes_one_tool(self) -> None:
        call = function_call(
            "open_application",
            '{"application_name":"Visual Studio Code"}',
        )
        client = fake_client(response([call]), response(text="Opening the app."))
        executor = Mock(
            return_value=ToolExecutionResult(True, "open_application", "opened")
        )

        route_request(
            "I need Visual Studio Code.",
            settings=self.settings,
            client=client,
            tool_executor=executor,
        )

        executor.assert_called_once_with(
            "open_application",
            '{"application_name":"Visual Studio Code"}',
        )

    def test_call_id_is_preserved_in_tool_output(self) -> None:
        call = function_call("open_website", '{"url":"nyu.edu"}', "call_keep_me")
        client = fake_client(response([call]), response(text="Done."))
        executor = Mock(
            return_value=ToolExecutionResult(True, "open_website", "opened")
        )

        route_request(
            "Open NYU.",
            settings=self.settings,
            client=client,
            tool_executor=executor,
        )

        second_input = client.responses.create.call_args_list[1].kwargs["input"]
        self.assertEqual(second_input[-1]["type"], "function_call_output")
        self.assertEqual(second_input[-1]["call_id"], "call_keep_me")

    def test_ordinary_text_response_is_displayed(self) -> None:
        client = fake_client(
            response(text="I can currently open websites and applications.")
        )

        result = route_request(
            "Delete my files.", settings=self.settings, client=client
        )

        self.assertEqual(
            result.message, "I can currently open websites and applications."
        )
        self.assertEqual(client.responses.create.call_count, 1)

    def test_missing_api_key_is_readable_and_makes_no_request(self) -> None:
        result = route_request("Open YouTube", settings=Settings(api_key=None))
        self.assertIn("OPENAI_API_KEY is not set", result.message)
        self.assertIn('export OPENAI_API_KEY="your_api_key_here"', result.message)

    def test_authentication_error_is_readable(self) -> None:
        authentication_error = type("AuthenticationError", (Exception,), {})
        client = fake_client(authentication_error())
        result = route_request("Open YouTube", settings=self.settings, client=client)
        self.assertIn("authentication failed", result.message)

    def test_rate_limit_error_is_readable(self) -> None:
        rate_limit_error = type("RateLimitError", (Exception,), {})
        client = fake_client(rate_limit_error())
        result = route_request("Open YouTube", settings=self.settings, client=client)
        self.assertIn("rate limit", result.message)

    def test_network_error_is_readable(self) -> None:
        connection_error = type("APIConnectionError", (Exception,), {})
        client = fake_client(connection_error())
        result = route_request("Open YouTube", settings=self.settings, client=client)
        self.assertIn("network", result.message)

    def test_parallel_tool_calls_are_disabled(self) -> None:
        client = fake_client(response(text="Unsupported request."))
        route_request("Do something", settings=self.settings, client=client)
        kwargs = client.responses.create.call_args.kwargs
        self.assertFalse(kwargs["parallel_tool_calls"])

    def test_exactly_two_strict_function_schemas_are_exposed(self) -> None:
        self.assertEqual([tool["name"] for tool in TOOLS], [
            "open_website",
            "open_application",
        ])
        for tool in TOOLS:
            self.assertTrue(tool["strict"])
            self.assertFalse(tool["parameters"]["additionalProperties"])

    def test_second_tool_request_is_not_executed(self) -> None:
        first = function_call("open_website", '{"url":"youtube.com"}', "call_1")
        second = function_call("open_application", '{"application_name":"Notes"}', "call_2")
        client = fake_client(response([first]), response([second]))
        executor = Mock(
            return_value=ToolExecutionResult(True, "open_website", "opened")
        )

        result = route_request(
            "Open YouTube.",
            settings=self.settings,
            client=client,
            tool_executor=executor,
        )

        executor.assert_called_once()
        self.assertIn("declined an additional", result.message)


if __name__ == "__main__":
    unittest.main()

