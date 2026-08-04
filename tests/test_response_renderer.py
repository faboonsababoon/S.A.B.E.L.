import unittest

from sabel.actions import ActionResult
from sabel.ollama_client import LocalToolCall
from sabel.response_renderer import SAFE_RENDERING_ERROR, render_visible_response


class ResponseRendererTests(unittest.TestCase):
    def setUp(self):
        self.internal_names = {
            "open_application",
            "open_service",
            "delegate_to_openai",
            "show_status",
            "empty_trash",
        }

    def test_internal_protocol_text_is_never_visible(self):
        examples = [
            "open_application /Applications/Safari.app",
            'delegate_to_openai {"task": "research"}',
            "ToolCall(name='open_service')",
            "ActionResult(success=True, message='done')",
            "BrowserResult(state=verified, success=True)",
            "BrowserOutcome(state=executed, message='done')",
            "RouterResultType.TOOL_CALLS",
            '{"application_name": "Safari"}',
            "Result: {'tab_id': 7, 'url': 'https://example.com'}",
        ]
        for value in examples:
            with self.subTest(value=value):
                self.assertEqual(
                    render_visible_response(value, self.internal_names),
                    SAFE_RENDERING_ERROR,
                )

    def test_dataclass_objects_are_never_stringified_for_display(self):
        values = [
            LocalToolCall("open_application", {"application_name": "Safari"}),
            ActionResult(True, "Opening Safari."),
        ]
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(
                    render_visible_response(value, self.internal_names),
                    SAFE_RENDERING_ERROR,
                )

    def test_normal_user_facing_text_is_preserved(self):
        self.assertEqual(
            render_visible_response("Opening Safari.", self.internal_names),
            "Opening Safari.",
        )


if __name__ == "__main__":
    unittest.main()
