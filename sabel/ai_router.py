"""Interpret flexible requests with the Responses API and approved tools."""

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from sabel.config import Settings, load_settings
from sabel.prompts import MODEL_INSTRUCTIONS
from sabel.tool_executor import ToolExecutionResult, execute_tool_call


TOOLS = [
    {
        "type": "function",
        "name": "open_website",
        "description": (
            "Open an HTTP or HTTPS website in the Mac's default browser. "
            "A domain without a scheme is allowed and will receive https:// locally."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The website domain or HTTP/HTTPS URL to open.",
                }
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "open_application",
        "description": "Open an installed macOS application by its application name.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "application_name": {
                    "type": "string",
                    "description": "The full application name, including spaces.",
                }
            },
            "required": ["application_name"],
            "additionalProperties": False,
        },
    },
]


MISSING_KEY_MESSAGE = """AI command interpretation is unavailable because OPENAI_API_KEY is not set.
In this Terminal session, run:
  export OPENAI_API_KEY="your_api_key_here"
This lasts only until you close the Terminal. You may add the same export line to ~/.zshrc yourself for future Terminal sessions.
Local commands such as help, quit, and explicit open commands still work."""


@dataclass(frozen=True)
class RouterResult:
    """The concise text SABEL should show after routing a request."""

    message: str
    used_tool: bool = False
    tool_result: Optional[ToolExecutionResult] = None


ToolExecutor = Callable[[str, str], ToolExecutionResult]


def _create_client(settings: Settings) -> Any:
    """Import the optional SDK only when an AI request is actually needed."""
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError(
            "The OpenAI package is not installed. Activate the virtual environment "
            "and run: python3 -m pip install -r requirements.txt"
        ) from error

    # OpenAI reads OPENAI_API_KEY from the environment. Disabling automatic
    # retries keeps this small command router predictable and avoids repeat costs.
    return OpenAI(max_retries=0, timeout=settings.timeout_seconds)


def _error_code(error: Exception) -> str:
    code = getattr(error, "code", "")
    if code:
        return str(code)
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested_error = body.get("error", body)
        if isinstance(nested_error, dict):
            return str(nested_error.get("code", ""))
    return ""


def _friendly_api_error(error: Exception, model: str) -> str:
    """Translate SDK/network failures without exposing secrets or raw responses."""
    error_name = type(error).__name__
    code = _error_code(error)

    if error_name == "AuthenticationError":
        return "OpenAI authentication failed. Check that OPENAI_API_KEY is valid."
    if error_name == "RateLimitError" and code == "insufficient_quota":
        return "OpenAI API credits or the project spending limit are insufficient."
    if error_name == "RateLimitError":
        return "The OpenAI rate limit was reached. Please wait and try again."
    if error_name in {"APITimeoutError", "TimeoutException"}:
        return "The OpenAI request timed out. Please try again."
    if error_name in {"APIConnectionError", "ConnectError", "NetworkError"}:
        return "SABEL could not connect to OpenAI. Check your network connection."
    if error_name in {"NotFoundError", "PermissionDeniedError"}:
        return f'The configured model "{model}" is unavailable to this API project.'
    if error_name == "BadRequestError":
        return "OpenAI rejected the request. Check SABEL_OPENAI_MODEL and try again."
    return "An unexpected OpenAI API error occurred. No local action was performed."


def _response_text(response: Any) -> str:
    text = getattr(response, "output_text", "")
    return text.strip() if isinstance(text, str) else ""


def _function_calls(response: Any) -> List[Any]:
    output = getattr(response, "output", []) or []
    return [item for item in output if getattr(item, "type", None) == "function_call"]


def _request_options(settings: Settings) -> dict:
    """Keep the Responses API configuration identical across both calls."""
    return {
        "model": settings.model,
        "instructions": MODEL_INSTRUCTIONS,
        "tools": TOOLS,
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "reasoning": {"effort": "low"},
        "max_output_tokens": 200,
        "store": False,
    }


def route_request(
    user_text: str,
    settings: Optional[Settings] = None,
    client: Any = None,
    tool_executor: ToolExecutor = execute_tool_call,
) -> RouterResult:
    """Perform at most one local tool call and one model continuation."""
    active_settings = settings or load_settings()
    if client is None and not active_settings.api_key:
        return RouterResult(MISSING_KEY_MESSAGE)

    try:
        active_client = client or _create_client(active_settings)
    except RuntimeError as error:
        return RouterResult(str(error))
    except Exception as error:
        return RouterResult(_friendly_api_error(error, active_settings.model))

    input_items: List[Any] = [{"role": "user", "content": user_text}]
    options = _request_options(active_settings)

    try:
        first_response = active_client.responses.create(
            input=input_items,
            **options,
        )
    except Exception as error:
        return RouterResult(_friendly_api_error(error, active_settings.model))

    calls = _function_calls(first_response)
    if not calls:
        ordinary_text = _response_text(first_response)
        return RouterResult(
            ordinary_text
            or "I can currently open websites and applications. Could you rephrase?"
        )
    if len(calls) != 1:
        return RouterResult("SABEL rejected multiple tool requests. No action was performed.")

    tool_call = calls[0]
    call_id = getattr(tool_call, "call_id", "")
    tool_name = getattr(tool_call, "name", "")
    arguments = getattr(tool_call, "arguments", "")
    if not call_id:
        return RouterResult("SABEL rejected a tool request without a call identifier.")

    tool_result = tool_executor(tool_name, arguments)
    continuation_input = input_items + list(getattr(first_response, "output", []) or [])
    continuation_input.append(
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": tool_result.to_json(),
        }
    )

    try:
        final_response = active_client.responses.create(
            input=continuation_input,
            **options,
        )
    except Exception as error:
        return RouterResult(
            f"{tool_result.message} "
            f"({_friendly_api_error(error, active_settings.model)})",
            used_tool=True,
            tool_result=tool_result,
        )

    # A second requested action is never executed; this is the hard lifecycle cap.
    if _function_calls(final_response):
        return RouterResult(
            f"{tool_result.message} SABEL declined an additional tool request.",
            used_tool=True,
            tool_result=tool_result,
        )

    return RouterResult(
        _response_text(final_response) or tool_result.message,
        used_tool=True,
        tool_result=tool_result,
    )
