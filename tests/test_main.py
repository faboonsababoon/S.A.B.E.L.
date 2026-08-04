import unittest

from main import render_result
from sabel.assistant import AssistantResult, AssistantResultType


class CommandLoopRenderingTests(unittest.TestCase):
    def test_typed_final_results_render_without_router_inference(self):
        for result_type in (
            AssistantResultType.RESPONSE,
            AssistantResultType.CLARIFICATION,
            AssistantResultType.TOOL_RESULT,
            AssistantResultType.ERROR,
        ):
            with self.subTest(result_type=result_type):
                rendered = render_result(AssistantResult(result_type, "visible"))
                self.assertEqual(rendered, "SABEL: visible")

    def test_unknown_result_type_is_not_rendered(self):
        class UnknownResult:
            result_type = "tool_calls"
            message = "empty_trash"

        rendered = render_result(UnknownResult())
        self.assertNotIn("empty_trash", rendered)


if __name__ == "__main__":
    unittest.main()
