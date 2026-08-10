import unittest
from unittest.mock import Mock

from sabel.browser_models import BrowserDecisionAction
from sabel.browser_planner import (
    BROWSER_DECISION_TOOL,
    BrowserPlannerError,
    OllamaBrowserPlanner,
    parse_browser_decision,
)
from sabel.browser_tasks import BrowserTaskManager
from sabel.ollama_client import LocalToolCall, OllamaResponse


class BrowserPlannerParsingTests(unittest.TestCase):
    def test_one_native_tool_call_becomes_one_typed_decision(self):
        client = Mock()
        client.chat.return_value = OllamaResponse(
            "",
            [
                LocalToolCall(
                    "browser_next_action",
                    {
                        "action": "click",
                        "element_ref": "element-7",
                        "reason": "open the matching documentation result",
                        "expected_result": "the documentation page opens",
                    },
                )
            ],
            2.0,
        )
        planner = OllamaBrowserPlanner(client)
        task = BrowserTaskManager().create(
            "find docs", "find docs", "personal", {"docs.python.org"}
        )
        decision = planner.next_action(
            task,
            {
                "snapshot_id": "snapshot-1",
                "tab_id": 1,
                "url": "https://docs.python.org/3/",
                "title": "Python docs",
                "visible_text_summary": "subprocess.run",
                "interactive_elements": [],
            },
        )
        self.assertEqual(decision.action, BrowserDecisionAction.CLICK)
        self.assertEqual(decision.element_ref, "element-7")
        messages, tools = client.chat.call_args.args
        self.assertEqual(tools, [BROWSER_DECISION_TOOL])
        self.assertIn("UNTRUSTED WEBPAGE DATA (BEGIN)", messages[-1]["content"])
        self.assertIn("Step: 1 / 15", messages[-1]["content"])

    def test_malformed_unknown_and_code_actions_fail_closed(self):
        invalid = (
            {},
            {"action": "evaluate_javascript", "reason": "run code", "code": "alert(1)"},
            {"action": "click", "reason": "click", "element_ref": "e", "selector": "#x"},
            {"action": "type", "reason": "type", "element_ref": "e"},
            {"action": "done", "reason": "done", "answer": "Found it", "evidence": []},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(BrowserPlannerError):
                parse_browser_decision(value)

    def test_multiple_calls_or_model_prose_is_rejected(self):
        task = BrowserTaskManager().create(
            "find docs", "find docs", "personal", {"docs.python.org"}
        )
        snapshot = {
            "url": "https://docs.python.org/",
            "title": "Docs",
            "visible_text_summary": "Docs",
            "interactive_elements": [],
        }
        for response in (
            OllamaResponse("click element 1", [], 1.0),
            OllamaResponse(
                "",
                [
                    LocalToolCall("browser_next_action", {"action": "back", "reason": "one"}),
                    LocalToolCall("browser_next_action", {"action": "back", "reason": "two"}),
                ],
                1.0,
            ),
        ):
            with self.subTest(response=response):
                client = Mock()
                client.chat.return_value = response
                with self.assertRaises(BrowserPlannerError):
                    OllamaBrowserPlanner(client).next_action(task, snapshot)

    def test_one_bounded_corrective_retry_can_recover_schema_omission(self):
        client = Mock()
        client.chat.side_effect = [
            OllamaResponse(
                "",
                [
                    LocalToolCall(
                        "browser_next_action",
                        {"action": "click", "reason": "open the result"},
                    )
                ],
                1.0,
            ),
            OllamaResponse(
                "",
                [
                    LocalToolCall(
                        "browser_next_action",
                        {"action": "back", "reason": "return to the prior page"},
                    )
                ],
                1.0,
            ),
        ]
        task = BrowserTaskManager().create(
            "find docs", "find docs", "personal", {"docs.python.org"}
        )
        decision = OllamaBrowserPlanner(client).next_action(
            task,
            {
                "url": "https://docs.python.org/",
                "title": "Docs",
                "visible_text_summary": "Docs",
                "interactive_elements": [],
            },
        )
        self.assertEqual(decision.action, BrowserDecisionAction.BACK)
        self.assertEqual(client.chat.call_count, 2)

    def test_safe_small_model_evidence_serialization_is_normalized(self):
        decision = parse_browser_decision(
            {
                "action": "done",
                "answer": "I found the section.",
                "evidence": (
                    '["title": "subprocess — Python documentation", '
                    '"visible_text_summary": "subprocess.run(args) Run the command"]'
                ),
            }
        )
        self.assertEqual(decision.action, BrowserDecisionAction.DONE)
        self.assertIn("propose done", decision.reason)
        self.assertEqual(
            decision.evidence,
            (
                "subprocess — Python documentation",
                "subprocess.run(args) Run the command",
            ),
        )

    def test_known_field_labels_inside_a_json_evidence_list_are_removed(self):
        decision = parse_browser_decision(
            {
                "action": "done",
                "answer": "I found the section.",
                "evidence": [
                    "title: subprocess — Python documentation",
                    "visible_text_summary: subprocess.run(args) Run the command",
                ],
            }
        )
        self.assertEqual(
            decision.evidence,
            (
                "subprocess — Python documentation",
                "subprocess.run(args) Run the command",
            ),
        )


if __name__ == "__main__":
    unittest.main()
