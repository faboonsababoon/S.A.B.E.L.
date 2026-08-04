"""Strict, versioned message and browser-command schemas."""

import json
import re
from typing import Callable


PROTOCOL_VERSION = 1
MAX_STRING_LENGTH = 4096
MAX_TYPED_TEXT_LENGTH = 2000
PROFILE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ALLOWED_PROFILE_IDS = {"personal", "nyu"}
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_KEYS = {"Enter", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Tab"}


class ProtocolError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_MESSAGE") -> None:
        super().__init__(message)
        self.code = code


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON fields are not allowed.", "DUPLICATE_FIELD")
        result[key] = value
    return result


def decode_json(raw: object) -> dict[str, object]:
    if not isinstance(raw, str):
        raise ProtocolError("Messages must be JSON text.")
    try:
        value = json.loads(raw, object_pairs_hook=_object_without_duplicates)
    except ProtocolError:
        raise
    except (json.JSONDecodeError, TypeError) as error:
        raise ProtocolError("Malformed JSON.", "MALFORMED_JSON") from error
    if not isinstance(value, dict):
        raise ProtocolError("The message envelope must be an object.")
    return value


def _strict_fields(
    message: dict[str, object], required: set[str], optional: set[str] = frozenset()
) -> None:
    allowed = required | optional
    if not required.issubset(message):
        missing = ", ".join(sorted(required - set(message)))
        raise ProtocolError(f"Missing required fields: {missing}.", "MISSING_FIELD")
    if not set(message).issubset(allowed):
        raise ProtocolError("Unknown message fields are not allowed.", "UNKNOWN_FIELD")


def _common(message: dict[str, object], expected_type: str) -> None:
    if message.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("Unsupported protocol version.", "UNSUPPORTED_VERSION")
    if message.get("type") != expected_type:
        raise ProtocolError("Unexpected message type.", "UNKNOWN_MESSAGE_TYPE")
    request_id = message.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise ProtocolError("Invalid request ID.", "INVALID_REQUEST_ID")


def validate_register(message: dict[str, object]) -> dict[str, object]:
    required = {
        "protocol_version",
        "type",
        "request_id",
        "token",
        "profile_id",
        "profile_name",
        "extension_version",
        "instance_id",
    }
    _strict_fields(message, required)
    _common(message, "register")
    profile_id = message["profile_id"]
    if (
        not isinstance(profile_id, str)
        or not PROFILE_ID_PATTERN.fullmatch(profile_id)
        or profile_id not in ALLOWED_PROFILE_IDS
    ):
        raise ProtocolError("Invalid browser profile ID.", "INVALID_PROFILE")
    for key, limit in (
        ("token", 256),
        ("profile_name", 64),
        ("extension_version", 32),
        ("instance_id", 128),
    ):
        value = message[key]
        if not isinstance(value, str) or not value or len(value) > limit:
            raise ProtocolError(f"Invalid {key}.")
    return message


def validate_client_message(message: dict[str, object]) -> dict[str, object]:
    message_type = message.get("type")
    if message_type == "response":
        _strict_fields(
            message,
            {"protocol_version", "type", "request_id", "profile_id", "success", "result", "error"},
        )
        _common(message, "response")
        if not isinstance(message["profile_id"], str):
            raise ProtocolError("Invalid profile ID.")
        if not isinstance(message["success"], bool):
            raise ProtocolError("Response success must be boolean.")
        if not isinstance(message["result"], dict):
            raise ProtocolError("Response result must be an object.")
        if message["error"] is not None and not isinstance(message["error"], dict):
            raise ProtocolError("Response error must be an object or null.")
        return message
    if message_type == "heartbeat":
        _strict_fields(message, {"protocol_version", "type", "request_id", "profile_id"})
        _common(message, "heartbeat")
        if not isinstance(message["profile_id"], str):
            raise ProtocolError("Invalid profile ID.")
        return message
    if message_type == "event":
        _strict_fields(
            message,
            {"protocol_version", "type", "request_id", "profile_id", "event", "payload"},
        )
        _common(message, "event")
        if message["event"] not in {"browser_control_stopped", "task_cancelled"}:
            raise ProtocolError("Unknown extension event.", "UNKNOWN_MESSAGE_TYPE")
        if not isinstance(message["payload"], dict):
            raise ProtocolError("Event payload must be an object.")
        return message
    raise ProtocolError("Unknown message type.", "UNKNOWN_MESSAGE_TYPE")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string(value: object, *, limit: int = MAX_STRING_LENGTH) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


ArgumentValidator = Callable[[dict[str, object]], None]


ACTION_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    "browser_list_profiles": (set(), set()),
    "browser_list_tabs": (set(), set()),
    "browser_get_active_tab": (set(), set()),
    "browser_open_tab": ({"url"}, {"active"}),
    "browser_navigate": ({"tab_id", "url"}, set()),
    "browser_activate_tab": ({"tab_id"}, set()),
    "browser_close_tab": ({"tab_id"}, set()),
    "browser_get_snapshot": ({"tab_id"}, set()),
    "browser_click": ({"tab_id", "snapshot_id", "element_id"}, set()),
    "browser_type": ({"tab_id", "snapshot_id", "element_id", "text"}, {"clear"}),
    "browser_select": ({"tab_id", "snapshot_id", "element_id", "value"}, set()),
    "browser_scroll": ({"tab_id", "delta_y"}, set()),
    "browser_press_key": ({"tab_id", "key"}, set()),
    "browser_go_back": ({"tab_id"}, set()),
    "browser_stop_task": (set(), {"task_id"}),
}


def validate_action(action: object, arguments: object) -> tuple[str, dict[str, object]]:
    if not isinstance(action, str) or action not in ACTION_FIELDS:
        raise ProtocolError("Unknown browser action.", "UNKNOWN_ACTION")
    if not isinstance(arguments, dict):
        raise ProtocolError("Browser action arguments must be an object.")
    required, optional = ACTION_FIELDS[action]
    _strict_fields(arguments, required, optional)

    for key in ("tab_id", "delta_y"):
        if key in arguments and not _is_int(arguments[key]):
            raise ProtocolError(f"{key} must be an integer.")
    if "tab_id" in arguments and int(arguments["tab_id"]) < 0:
        raise ProtocolError("tab_id must be non-negative.")
    if "delta_y" in arguments and abs(int(arguments["delta_y"])) > 5000:
        raise ProtocolError("Scroll distance is too large.")
    for key in ("url", "snapshot_id", "element_id", "value", "task_id"):
        if key in arguments and not _string(arguments[key]):
            raise ProtocolError(f"Invalid {key}.")
    if "text" in arguments and not _string(arguments["text"], limit=MAX_TYPED_TEXT_LENGTH):
        raise ProtocolError("Typed text is empty or too long.")
    for key in ("active", "clear"):
        if key in arguments and not isinstance(arguments[key], bool):
            raise ProtocolError(f"{key} must be boolean.")
    if "key" in arguments and arguments["key"] not in SAFE_KEYS:
        raise ProtocolError("That key is not permitted.", "UNSAFE_KEY")
    return action, dict(arguments)


def command_message(
    request_id: str,
    profile_id: str,
    action: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    validate_action(action, arguments)
    if profile_id not in ALLOWED_PROFILE_IDS:
        raise ProtocolError("Invalid browser profile ID.", "INVALID_PROFILE")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": "command",
        "request_id": request_id,
        "profile_id": profile_id,
        "action": action,
        "arguments": arguments,
    }


def server_message(message_type: str, request_id: str, **values) -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": message_type,
        "request_id": request_id,
        **values,
    }
