import json
import unittest

from sabel.browser_protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    command_message,
    decode_json,
    validate_action,
    validate_client_message,
    validate_register,
)


def registration(**changes):
    message = {
        "protocol_version": PROTOCOL_VERSION,
        "type": "register",
        "request_id": "register-1",
        "token": "token-value",
        "profile_id": "personal",
        "profile_name": "Personal",
        "extension_version": "0.1.0",
        "instance_id": "instance-1",
    }
    message.update(changes)
    return message


class BrowserProtocolTests(unittest.TestCase):
    def test_valid_registration_and_command_envelopes(self):
        self.assertEqual(validate_register(registration())["profile_id"], "personal")
        command = command_message(
            "request-1", "personal", "browser_get_snapshot", {"tab_id": 4}
        )
        self.assertEqual(command["protocol_version"], 1)
        self.assertEqual(command["type"], "command")
        with self.assertRaisesRegex(ProtocolError, "profile"):
            command_message("request-2", "work", "browser_list_tabs", {})

    def test_wrong_version_and_unknown_message_type_are_rejected(self):
        with self.assertRaisesRegex(ProtocolError, "Unsupported"):
            validate_register(registration(protocol_version=2))
        with self.assertRaisesRegex(ProtocolError, "Unknown message"):
            validate_client_message(
                {
                    "protocol_version": 1,
                    "type": "do_anything",
                    "request_id": "x",
                }
            )

    def test_missing_and_unknown_registration_fields_are_rejected(self):
        missing = registration()
        missing.pop("token")
        with self.assertRaises(ProtocolError):
            validate_register(missing)
        with self.assertRaisesRegex(ProtocolError, "Unknown"):
            validate_register(registration(extra="no"))
        with self.assertRaisesRegex(ProtocolError, "profile"):
            validate_register(registration(profile_id="work"))

    def test_malformed_json_and_duplicate_fields_are_rejected(self):
        with self.assertRaisesRegex(ProtocolError, "Malformed"):
            decode_json("{")
        with self.assertRaisesRegex(ProtocolError, "Duplicate"):
            decode_json('{"type":"response","type":"event"}')

    def test_browser_actions_have_strict_arguments(self):
        validate_action(
            "browser_click",
            {"tab_id": 1, "snapshot_id": "snapshot-1", "element_id": "element-1"},
        )
        with self.assertRaisesRegex(ProtocolError, "Unknown message fields"):
            validate_action(
                "browser_click",
                {
                    "tab_id": 1,
                    "snapshot_id": "snapshot-1",
                    "element_id": "element-1",
                    "selector": "#arbitrary",
                },
            )
        with self.assertRaisesRegex(ProtocolError, "Unknown browser action"):
            validate_action("evaluate_javascript", {"code": "alert(1)"})

    def test_typing_and_key_limits(self):
        with self.assertRaisesRegex(ProtocolError, "too long"):
            validate_action(
                "browser_type",
                {
                    "tab_id": 1,
                    "snapshot_id": "s",
                    "element_id": "e",
                    "text": "x" * 2001,
                },
            )
        with self.assertRaisesRegex(ProtocolError, "not permitted"):
            validate_action("browser_press_key", {"tab_id": 1, "key": "F12"})

    def test_response_schema_rejects_unknown_fields(self):
        message = {
            "protocol_version": 1,
            "type": "response",
            "request_id": "request-1",
            "profile_id": "personal",
            "success": True,
            "result": {},
            "error": None,
            "token": "must-not-be-here",
        }
        with self.assertRaisesRegex(ProtocolError, "Unknown"):
            validate_client_message(message)


if __name__ == "__main__":
    unittest.main()
