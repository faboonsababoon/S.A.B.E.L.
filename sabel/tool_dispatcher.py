"""Validate Ollama tool calls and dispatch only reviewed local operations."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Set

from sabel import actions
from sabel.actions import ActionResult, normalize_website
from sabel.config import Settings
from sabel.confirmations import (
    TRASH_DESCRIPTION,
    TRASH_WARNING,
    is_explicit_trash_confirmation,
    normalize_confirmation_arguments,
)
from sabel.conversation_state import ConversationState


@dataclass(frozen=True)
class DelegationRequest:
    task: str
    reason: str
    requires_current_web_information: bool


@dataclass(frozen=True)
class DispatchResult:
    message: str
    should_exit: bool = False
    delegation: Optional[DelegationRequest] = None
    selected_tool: Optional[str] = None
    validated_arguments: Optional[Dict[str, Any]] = None
    continue_with_new_request: bool = False


def _valid_keys(arguments: Dict[str, Any], required: Set[str], optional=None) -> bool:
    allowed = required | (optional or set())
    return required.issubset(arguments) and set(arguments).issubset(allowed)


class ToolDispatcher:
    def __init__(
        self,
        settings: Settings,
        state: ConversationState,
        ollama_available: Callable[[], bool],
        handlers: Optional[Dict[str, Callable[..., ActionResult]]] = None,
    ) -> None:
        self.settings = settings
        self.state = state
        self.ollama_available = ollama_available
        self.handlers = handlers or {
            "open_website": actions.open_website,
            "open_application": actions.open_application,
            "open_youtube_search": actions.open_youtube_search,
            "open_web_search": actions.open_web_search,
            "empty_trash": actions.empty_trash,
        }

    def dispatch(self, name: str, arguments: Dict[str, Any], user_text: str) -> DispatchResult:
        if not isinstance(arguments, dict):
            return DispatchResult("SABEL rejected malformed tool arguments.")

        if name == "open_website":
            return self._one_string(name, arguments, "url", self.handlers[name])
        if name == "open_application":
            return self._one_string(name, arguments, "application_name", self.handlers[name])
        if name == "open_youtube_search":
            return self._one_string(name, arguments, "query", self.handlers[name])
        if name == "open_web_search":
            if not _valid_keys(arguments, {"query"}, {"search_engine"}):
                return DispatchResult("The web-search arguments were rejected.")
            query = arguments.get("query")
            engine = arguments.get("search_engine", "google")
            if not isinstance(query, str) or not isinstance(engine, str):
                return DispatchResult("The web-search arguments must be text.")
            result = self.handlers[name](query.strip(), engine.strip())
            return DispatchResult(result.message, selected_tool=name, validated_arguments={"query": query.strip(), "search_engine": engine.strip()})
        if name == "empty_trash":
            if arguments:
                return DispatchResult("The empty_trash tool accepts no arguments.")
            self.state.set_pending(
                "empty_trash",
                {},
                description=TRASH_DESCRIPTION,
                warning=TRASH_WARNING,
            )
            return DispatchResult(TRASH_WARNING, selected_tool=name, validated_arguments={})
        if name == "show_capabilities":
            if arguments:
                return DispatchResult("The capabilities tool accepts no arguments.")
            return DispatchResult(
                "I can open websites and apps, search YouTube or the web, show status, "
                "open stored research sources, and empty Trash after explicit confirmation. "
                "Complex current research can be delegated according to cloud mode.",
                selected_tool=name,
                validated_arguments={},
            )
        if name == "show_status":
            if arguments:
                return DispatchResult("The status tool accepts no arguments.")
            cloud_mode_label = {
                "off": "Off",
                "ask": "Ask",
                "auto": "Automatic",
            }[self.settings.cloud_mode]
            status = (
                "SABEL status\n"
                "Local model provider: Ollama\n"
                f"Local model: {self.settings.ollama_model}\n"
                f"Ollama connection: {'Available' if self.ollama_available() else 'Unavailable'}\n"
                f"Cloud mode: {cloud_mode_label}\n"
                f"OpenAI configured: {'Yes' if self.settings.openai_api_key else 'No'}\n"
                f"Cloud model: {self.settings.openai_model}\n"
                "Cloud API used for this command: No"
            )
            return DispatchResult(status, selected_tool=name, validated_arguments={})
        if name == "exit_assistant":
            if arguments:
                return DispatchResult("The exit tool accepts no arguments.")
            return DispatchResult("SABEL is going offline.", should_exit=True, selected_tool=name, validated_arguments={})
        if name == "delegate_to_openai":
            required = {"task", "reason", "requires_current_web_information"}
            if not _valid_keys(arguments, required):
                return DispatchResult("The cloud-delegation arguments were rejected.")
            task, reason, current = arguments["task"], arguments["reason"], arguments["requires_current_web_information"]
            if not isinstance(task, str) or not task.strip() or not isinstance(reason, str) or not isinstance(current, bool):
                return DispatchResult("The cloud-delegation arguments were rejected.")
            delegation = DelegationRequest(task.strip(), reason.strip(), current)
            return DispatchResult("", delegation=delegation, selected_tool=name, validated_arguments=arguments)
        if name == "open_research_source":
            if not _valid_keys(arguments, {"source_number"}):
                return DispatchResult("The research-source arguments were rejected.")
            number = arguments["source_number"]
            if not isinstance(number, int) or isinstance(number, bool):
                return DispatchResult("The source number must be an integer.")
            url, error = self.state.resolve_source(number)
            if error:
                return DispatchResult(error)
            try:
                validated = normalize_website(url or "")
            except ValueError as validation_error:
                return DispatchResult(str(validation_error))
            result = self.handlers["open_website"](validated)
            return DispatchResult(result.message, selected_tool=name, validated_arguments={"source_number": number})
        return DispatchResult("SABEL rejected an unknown local tool request.")

    def _one_string(self, name: str, arguments: Dict[str, Any], key: str, handler) -> DispatchResult:
        if not _valid_keys(arguments, {key}) or not isinstance(arguments.get(key), str) or not arguments[key].strip():
            return DispatchResult(f"The {name} arguments were rejected.")
        value = arguments[key].strip()
        result = handler(value)
        return DispatchResult(result.message, selected_tool=name, validated_arguments={key: value})

    def dispatch_pending(
        self, name: str, arguments: Dict[str, Any], user_text: str
    ) -> DispatchResult:
        """Handle only a decision produced by the dedicated pending interpreter."""
        pending = self.state.pending_action
        if pending is None:
            return DispatchResult("There is no pending action to handle.")
        if name == "cancel_pending_action":
            if arguments:
                return DispatchResult("The cancellation tool accepts no arguments.")
            self.state.clear_pending()
            return DispatchResult("Trash emptying cancelled.", selected_tool=name, validated_arguments={})
        if name == "explain_pending_action":
            if arguments:
                return DispatchResult("The explanation tool accepts no arguments.")
            return DispatchResult(
                pending.description,
                selected_tool=name,
                validated_arguments={},
            )
        if name == "route_new_request":
            if arguments:
                return DispatchResult("The new-request tool accepts no arguments.")
            self.state.clear_pending()
            return DispatchResult(
                "Cancelled the pending Trash action.",
                selected_tool=name,
                validated_arguments={},
                continue_with_new_request=True,
            )
        if name != "confirm_pending_action":
            return DispatchResult("SABEL rejected an unknown pending-action tool request.")

        normalized = normalize_confirmation_arguments(arguments, pending.tool_name)
        if normalized is None:
            return DispatchResult(
                "The confirmation arguments were rejected; the pending action was not executed."
            )
        if not is_explicit_trash_confirmation(user_text):
            return DispatchResult(TRASH_WARNING)

        handler = self.handlers.get(pending.tool_name)
        if handler is None:
            self.state.clear_pending()
            return DispatchResult("The stored pending action is no longer available.")
        try:
            result = handler(**pending.arguments)
        except Exception:
            result = ActionResult(False, "I could not empty Trash because macOS denied the operation.")
        finally:
            self.state.clear_pending()
        return DispatchResult(
            result.message,
            selected_tool=name,
            validated_arguments=normalized,
        )
