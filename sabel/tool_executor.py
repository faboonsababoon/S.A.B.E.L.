"""Validate and execute only the two local actions approved for the model."""

from dataclasses import asdict, dataclass
import json
from typing import Any, Callable, Dict

from sabel.actions import ActionResult, normalize_website, open_application, open_website


@dataclass(frozen=True)
class ToolExecutionResult:
    """A structured result returned to the model after local execution."""

    success: bool
    tool_name: str
    message: str

    def to_json(self) -> str:
        """Serialize the result for a function_call_output item."""
        return json.dumps(asdict(self))


Action = Callable[[str], ActionResult]


def _failure(tool_name: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(False, tool_name, message)


def _has_exact_argument(arguments: Dict[str, Any], required_name: str) -> bool:
    """Enforce the local equivalent of the tools' strict JSON schemas."""
    return set(arguments) == {required_name}


def execute_tool_call(
    tool_name: str,
    arguments_json: str,
    website_action: Action = open_website,
    application_action: Action = open_application,
) -> ToolExecutionResult:
    """Parse, allowlist, validate, and execute one model-requested tool."""
    if tool_name not in {"open_website", "open_application"}:
        return _failure(tool_name, "SABEL rejected an unknown tool request.")

    try:
        arguments = json.loads(arguments_json)
    except (json.JSONDecodeError, TypeError):
        return _failure(tool_name, "SABEL rejected malformed tool arguments.")

    if not isinstance(arguments, dict):
        return _failure(tool_name, "Tool arguments must be a JSON object.")

    if tool_name == "open_website":
        if not _has_exact_argument(arguments, "url"):
            return _failure(tool_name, "The open_website tool requires only a url.")
        url = arguments["url"]
        if not isinstance(url, str):
            return _failure(tool_name, "The website URL must be text.")
        try:
            validated_url = normalize_website(url)
        except ValueError as error:
            return _failure(tool_name, str(error))
        action_result = website_action(validated_url)
    else:
        if not _has_exact_argument(arguments, "application_name"):
            return _failure(
                tool_name,
                "The open_application tool requires only an application_name.",
            )
        application_name = arguments["application_name"]
        if not isinstance(application_name, str) or not application_name.strip():
            return _failure(tool_name, "Please provide an application name.")
        action_result = application_action(application_name.strip())

    return ToolExecutionResult(
        action_result.success,
        tool_name,
        action_result.message,
    )

