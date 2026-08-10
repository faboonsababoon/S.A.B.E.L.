"""Validate Ollama tool calls and dispatch only reviewed local operations."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Set

from sabel import actions
from sabel.actions import ActionResult, normalize_application_name, normalize_website
from sabel.application_catalog import ApplicationCatalog
from sabel.browser_models import BrowserActionContext
from sabel.browser_routing import (
    WEB_SEARCH_ENGINES,
    normalize_search_engine,
    validate_search_query,
)
from sabel.config import Settings
from sabel.confirmations import (
    TRASH_DESCRIPTION,
    TRASH_WARNING,
    is_explicit_trash_confirmation,
    normalize_confirmation_arguments,
)
from sabel.conversation_state import ConversationState
from sabel.services import (
    LEGACY_OPEN_SERVICE_NAMES,
    normalize_service_name,
    resolve_service,
)


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
    action_success: Optional[bool] = None
    action_state: Optional[str] = None
    verified: bool = False
    clarification_intent: Optional[str] = None
    clarification_slots: Optional[Dict[str, Any]] = None
    clarification_missing: Optional[list[str]] = None
    browser_context: Optional[BrowserActionContext] = None
    browser_debug: Optional[Dict[str, Any]] = None
    application_debug: Optional[Dict[str, Any]] = None


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
        browser_runtime=None,
        application_catalog: Optional[ApplicationCatalog] = None,
    ) -> None:
        self.settings = settings
        self.state = state
        self.ollama_available = ollama_available
        self.browser_runtime = browser_runtime
        self.application_catalog = application_catalog or ApplicationCatalog()
        self._resolve_installed_applications = handlers is None or application_catalog is not None
        self.handlers = handlers or {
            "open_service": actions.open_service,
            "open_website": actions.open_website,
            "open_application": actions.open_application,
            "open_spotify_search": actions.open_spotify_search,
            "open_youtube_search": actions.open_youtube_search,
            "open_web_search": actions.open_web_search,
            "empty_trash": actions.empty_trash,
            "get_trash_status": actions.get_trash_status,
        }

    def dispatch(
        self,
        name: str,
        arguments: Dict[str, Any],
        user_text: str,
        approval_callback=None,
    ) -> DispatchResult:
        if not isinstance(arguments, dict):
            return DispatchResult("SABEL rejected malformed tool arguments.")

        if name == "show_browser_profiles":
            if arguments:
                return DispatchResult("The browser-profile tool accepts no arguments.")
            return self._browser_outcome(name, {}, self._browser_call("show_profiles"))
        if name == "show_browser_tabs":
            if not _valid_keys(arguments, {"profile"}):
                return DispatchResult("The browser-tab arguments were rejected.")
            profile = arguments.get("profile")
            if not isinstance(profile, str) or profile.strip().casefold() not in {"personal", "nyu"}:
                return DispatchResult("The browser profile must be Personal or NYU.")
            selected_profile = profile.strip().casefold()
            return self._browser_outcome(
                name,
                {"profile": selected_profile},
                self._browser_call("show_browser_tabs", selected_profile),
            )
        if name in {"open_service", "open_service_in_profile"}:
            if (
                name == "open_service"
                and self.browser_runtime is None
                and _valid_keys(arguments, {"service_name"}, {"browser"})
            ):
                return self._legacy_open_service(arguments)
            if name == "open_service":
                if not _valid_keys(arguments, {"service_name", "profile_id"}):
                    return DispatchResult("The browser service arguments were rejected.")
                service = arguments.get("service_name")
                profile = arguments.get("profile_id")
            else:
                if not _valid_keys(arguments, {"service"}, {"profile"}):
                    return DispatchResult("The browser service arguments were rejected.")
                service = arguments.get("service")
                profile = arguments.get("profile")
            if not isinstance(service, str) or not service.strip():
                return DispatchResult("The browser service arguments were rejected.")
            if name == "open_service_in_profile" and profile is None:
                validated = {"service": service.strip()}
                outcome = self._browser_call(
                    "open_service_in_profile", service.strip(), None
                )
                return self._browser_outcome(name, validated, outcome)
            if not isinstance(profile, str) or profile.strip().casefold() not in {
                "personal",
                "nyu",
            }:
                return DispatchResult("The browser profile must be Personal or NYU.")
            selected_profile = profile.strip().casefold()
            if name == "open_service_in_profile":
                validated = {
                    "service": service.strip(),
                    "profile": selected_profile,
                }
                outcome = self._browser_call(
                    "open_service_in_profile", service.strip(), selected_profile
                )
                return self._browser_outcome(name, validated, outcome)
            validated = {
                "service_name": service.strip(),
                "profile_id": selected_profile,
            }
            outcome = self._browser_call(
                "open_service", service.strip(), selected_profile
            )
            return self._browser_outcome(name, validated, outcome)
        if name == "search_web":
            if not _valid_keys(
                arguments, {"query", "search_engine", "profile_id"}
            ):
                return DispatchResult("The browser-search arguments were rejected.")
            supplied_engine = arguments.get("search_engine")
            if (
                isinstance(supplied_engine, str)
                and supplied_engine.strip().casefold() == "default"
            ):
                engine = self.settings.default_search_engine
                if engine is None:
                    return DispatchResult(
                        "No default browser search engine is configured. Please choose Google, Bing, or DuckDuckGo."
                    )
            else:
                engine = normalize_search_engine(supplied_engine)
            profile = arguments.get("profile_id")
            try:
                query = validate_search_query(arguments.get("query"))
            except ValueError as error:
                return DispatchResult(str(error))
            if engine not in WEB_SEARCH_ENGINES:
                return DispatchResult(
                    "The browser search engine must be Google, Bing, or DuckDuckGo."
                )
            if not isinstance(profile, str) or profile.strip().casefold() not in {
                "personal",
                "nyu",
            }:
                return DispatchResult("The browser profile must be Personal or NYU.")
            profile_id = profile.strip().casefold()
            validated = {
                "query": query,
                "search_engine": engine,
                "profile_id": profile_id,
            }
            return self._browser_outcome(
                name,
                validated,
                self._browser_call("search_web", query, engine, profile_id),
            )
        if name == "search_youtube":
            if not _valid_keys(arguments, {"query", "profile_id"}):
                return DispatchResult("The YouTube-search arguments were rejected.")
            profile = arguments.get("profile_id")
            try:
                query = validate_search_query(arguments.get("query"))
            except ValueError as error:
                return DispatchResult(str(error))
            if not isinstance(profile, str) or profile.strip().casefold() not in {
                "personal",
                "nyu",
            }:
                return DispatchResult("The browser profile must be Personal or NYU.")
            profile_id = profile.strip().casefold()
            validated = {"query": query, "profile_id": profile_id}
            return self._browser_outcome(
                name,
                validated,
                self._browser_call("search_youtube", query, profile_id),
            )
        if name == "browser_search":
            if not _valid_keys(arguments, {"service", "query"}, {"profile"}):
                return DispatchResult("The browser-search arguments were rejected.")
            service = arguments.get("service")
            query = arguments.get("query")
            profile = arguments.get("profile")
            engine = normalize_search_engine(service)
            try:
                cleaned_query = validate_search_query(query)
            except ValueError as error:
                return DispatchResult(str(error))
            if engine not in {*WEB_SEARCH_ENGINES, "youtube"}:
                return DispatchResult("The browser-search arguments were rejected.")
            if profile is not None and (
                not isinstance(profile, str)
                or profile.strip().casefold() not in {"personal", "nyu"}
            ):
                return DispatchResult("The browser profile must be Personal or NYU.")
            selected_profile = profile.strip().casefold() if isinstance(profile, str) else None
            validated = {"service": engine, "query": cleaned_query}
            if selected_profile:
                validated["profile"] = selected_profile
            return self._browser_outcome(
                name,
                validated,
                self._browser_call(
                    "browser_search", engine, cleaned_query, selected_profile
                ),
            )
        if name == "browser_copilot_task":
            if not _valid_keys(
                arguments,
                {"objective", "profile"},
                {"service", "initial_url"},
            ):
                return DispatchResult("The browser-task arguments were rejected.")
            objective = arguments.get("objective")
            service = arguments.get("service")
            profile = arguments.get("profile")
            initial_url = arguments.get("initial_url")
            if not all(
                isinstance(value, str) and value.strip()
                for value in (objective, profile)
            ):
                return DispatchResult("The browser-task arguments were rejected.")
            if bool(isinstance(service, str) and service.strip()) == bool(
                isinstance(initial_url, str) and initial_url.strip()
            ):
                return DispatchResult(
                    "A browser task requires exactly one reviewed service or explicit URL."
                )
            selected_profile = profile.strip().casefold()
            if selected_profile not in {"personal", "nyu"}:
                return DispatchResult("The browser profile must be Personal or NYU.")
            validated = {
                "objective": objective.strip(),
                "profile": selected_profile,
            }
            selected_service = None
            selected_url = None
            if isinstance(service, str) and service.strip():
                selected_service = service.strip().casefold()
                validated["service"] = selected_service
            if isinstance(initial_url, str) and initial_url.strip():
                from sabel.browser_security import BrowserSecurityError, validate_http_url

                try:
                    selected_url = validate_http_url(initial_url)
                except BrowserSecurityError as error:
                    return DispatchResult(str(error))
                if selected_url not in user_text:
                    return DispatchResult(
                        "The browser task URL must be explicitly present in your request."
                    )
                validated["initial_url"] = selected_url
            call_arguments = [
                validated["objective"],
                selected_service,
                selected_profile,
                approval_callback,
            ]
            if selected_url is not None:
                call_arguments.append(selected_url)
            return self._browser_outcome(
                name,
                validated,
                self._browser_call("browser_copilot_task", *call_arguments),
            )
        if name == "stop_browser_task":
            if arguments:
                return DispatchResult("The stop-browser tool accepts no arguments.")
            return self._browser_outcome(
                name, {}, self._browser_call("stop_browser_task")
            )
        if name == "show_recent_browser_actions":
            if arguments:
                return DispatchResult("The browser-audit tool accepts no arguments.")
            return self._browser_outcome(
                name, {}, self._browser_call("show_recent_browser_actions")
            )

        if name == "open_website":
            return self._one_string(name, arguments, "url", self.handlers[name])
        if name == "check_application_installed":
            if not _valid_keys(arguments, {"application_name"}):
                return DispatchResult("The application-check arguments were rejected.")
            supplied_name = arguments.get("application_name")
            if not isinstance(supplied_name, str):
                return DispatchResult("The application-check arguments were rejected.")
            try:
                application_name = normalize_application_name(supplied_name)
            except ValueError as error:
                return DispatchResult(str(error))
            match = self.application_catalog.resolve(application_name)
            application_debug = {
                "requested_application": application_name,
                "selected_application": (
                    match.application.display_name if match.application else None
                ),
                "candidates": [
                    {
                        "display_name": candidate.display_name,
                        "score": candidate.score,
                        "accepted": candidate.accepted,
                        "reason": candidate.reason,
                    }
                    for candidate in match.candidates
                ],
            }
            if match.application is not None:
                display_name = match.application.display_name
                message = f"Yes—{display_name} is installed."
            else:
                display_name = application_name
                message = match.message or (
                    f"I could not find an installed application named {application_name}."
                )
            return DispatchResult(
                message,
                selected_tool=name,
                validated_arguments={"application_name": display_name},
                action_success=True,
                verified=True,
                application_debug=application_debug,
            )
        if name == "show_installed_applications":
            if arguments:
                return DispatchResult("The installed-applications tool accepts no arguments.")
            display_names = sorted(
                {application.display_name for application in self.application_catalog.applications()},
                key=str.casefold,
            )
            limit = 60
            visible_names = display_names[:limit]
            lines = [f"Installed applications ({len(display_names)})"]
            lines.extend(f"- {display_name}" for display_name in visible_names)
            if len(display_names) > limit:
                lines.append(
                    f"…and {len(display_names) - limit} more. Ask whether a specific application is installed."
                )
            return DispatchResult(
                "\n".join(lines),
                selected_tool=name,
                validated_arguments={},
                action_success=True,
                verified=True,
            )
        if name == "open_application":
            if not _valid_keys(arguments, {"application_name"}):
                return DispatchResult("The application arguments were rejected.")
            supplied_name = arguments.get("application_name")
            if not isinstance(supplied_name, str):
                return DispatchResult("The application arguments were rejected.")
            try:
                application_name = normalize_application_name(supplied_name)
            except ValueError as error:
                return DispatchResult(str(error))
            application_debug = None
            if self._resolve_installed_applications:
                match = self.application_catalog.resolve(application_name)
                application_debug = {
                    "requested_application": application_name,
                    "selected_application": (
                        match.application.display_name if match.application else None
                    ),
                    "candidates": [
                        {
                            "display_name": candidate.display_name,
                            "score": candidate.score,
                            "accepted": candidate.accepted,
                            "reason": candidate.reason,
                        }
                        for candidate in match.candidates
                    ],
                }
                if match.application is None:
                    return DispatchResult(
                        match.message or f"I could not find an installed application named {application_name}.",
                        selected_tool=name,
                        validated_arguments={"application_name": application_name},
                        action_success=False,
                        application_debug=application_debug,
                    )
                execution_name = match.application.launch_name
                display_name = match.application.display_name
            else:
                execution_name = application_name
                display_name = application_name
            result = self.handlers[name](execution_name)
            message = (
                f"Opening {display_name}."
                if result.success and self._resolve_installed_applications
                else result.message
            )
            return DispatchResult(
                message,
                selected_tool=name,
                validated_arguments={"application_name": display_name},
                action_success=result.success,
                verified=result.success,
                application_debug=application_debug,
            )
        if name == "open_service_default_browser":
            return self._legacy_open_service(arguments, selected_name=name)
        if name == "open_spotify_search":
            return self._one_string(name, arguments, "query", self.handlers[name])
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
            return DispatchResult(
                result.message,
                selected_tool=name,
                validated_arguments={
                    "query": query.strip(),
                    "search_engine": engine.strip(),
                },
                action_success=result.success,
            )
        if name == "empty_trash":
            if arguments:
                return DispatchResult("The empty_trash tool accepts no arguments.")
            self.state.set_pending_destructive_action(
                "empty_trash",
                {},
                description=TRASH_DESCRIPTION,
                warning=TRASH_WARNING,
            )
            return DispatchResult(TRASH_WARNING, selected_tool=name, validated_arguments={})
        if name == "get_trash_status":
            if arguments:
                return DispatchResult("The Trash-status tool accepts no arguments.")
            result = self.handlers[name]()
            return DispatchResult(
                result.message,
                selected_tool=name,
                validated_arguments={},
                action_success=result.success,
            )
        if name == "show_capabilities":
            if arguments:
                return DispatchResult("The capabilities tool accepts no arguments.")
            return DispatchResult(
                "I can use connected Personal and NYU Chrome profiles for reviewed services, "
                "tab summaries, searches, verified YouTube channel navigation, and bounded "
                "multi-step same-domain browser tasks; open websites "
                "and installed apps; inspect the installed-app catalog; show status; "
                "inspect Trash, open stored research sources, and empty Trash after explicit confirmation. "
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
                f"Local model: {self.settings.ollama_model}\n"
                f"Ollama: {'Available' if self.ollama_available() else 'Unavailable'}\n"
                f"Cloud mode: {cloud_mode_label}\n"
                f"OpenAI configured: {'Yes' if self.settings.openai_api_key else 'No'}\n"
                "Pending destructive action: "
                f"{'Pending' if self.state.pending_destructive_action else 'None'}\n"
                "Pending clarification: "
                f"{'Pending' if self.state.pending_clarification or self.state.pending_media_request else 'None'}"
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
            return DispatchResult(
                result.message,
                selected_tool=name,
                validated_arguments={"source_number": number},
                action_success=result.success,
            )
        return DispatchResult("SABEL rejected an unknown local tool request.")

    def _legacy_open_service(
        self, arguments: Dict[str, Any], *, selected_name: str = "open_service"
    ) -> DispatchResult:
        if not _valid_keys(arguments, {"service_name"}, {"browser"}):
            return DispatchResult("The service arguments were rejected.")
        supplied_service = arguments.get("service_name")
        supplied_browser = arguments.get("browser", "")
        if not isinstance(supplied_service, str) or not isinstance(
            supplied_browser, str
        ):
            return DispatchResult("The service arguments were rejected.")
        try:
            service_name = normalize_service_name(supplied_service)
        except ValueError as error:
            return DispatchResult(str(error))
        if (
            service_name not in LEGACY_OPEN_SERVICE_NAMES
            or resolve_service(service_name) is None
        ):
            return DispatchResult(
                "I do not recognize that service. Try YouTube, Google, or GitHub."
            )
        browser = ""
        if supplied_browser.strip():
            try:
                browser = normalize_application_name(supplied_browser)
            except ValueError as error:
                return DispatchResult(str(error))
        result = self.handlers["open_service"](service_name, browser)
        validated = {"service_name": service_name}
        if browser:
            validated["browser"] = browser
        return DispatchResult(
            result.message,
            selected_tool=selected_name,
            validated_arguments=validated,
            action_success=result.success,
        )

    def _browser_call(self, method_name: str, *arguments):
        if self.browser_runtime is None:
            return None
        try:
            return getattr(self.browser_runtime, method_name)(*arguments)
        except Exception as error:
            from sabel.browser_copilot import BrowserOutcome
            from sabel.browser_models import BrowserActionState

            return BrowserOutcome(
                BrowserActionState.FAILED,
                f"The local browser bridge could not complete that action: {error}",
                False,
            )

    def cancel_expired_browser_confirmation(self, task_id: object) -> None:
        if isinstance(task_id, str) and task_id:
            self._browser_call("cancel_pending_browser_task", task_id)

    def _browser_outcome(self, name: str, validated: Dict[str, Any], outcome) -> DispatchResult:
        if outcome is None:
            return DispatchResult(
                "Browser Copilot is unavailable because the local bridge is not running.",
                selected_tool=name,
                validated_arguments=validated,
                action_success=False,
                action_state="failed",
            )
        message = getattr(outcome, "message", None)
        state = getattr(outcome, "state", None)
        state_value = getattr(state, "value", None)
        if not isinstance(message, str) or state_value not in {
            "planned", "requested", "executed", "verified", "failed", "cancelled"
        }:
            return DispatchResult("SABEL rejected a malformed browser result.")
        clarification = getattr(outcome, "clarification", None)
        confirmation_prompt = getattr(outcome, "confirmation_prompt", None)
        task_id = getattr(outcome, "task_id", None)
        if confirmation_prompt:
            if not isinstance(task_id, str) or not task_id:
                return DispatchResult("SABEL rejected malformed browser confirmation state.")
            self.state.set_pending_destructive_action(
                "confirm_browser_task",
                {"task_id": task_id},
                description=(
                    "A browser task is paused before a consequential page action. "
                    "Only the exact reviewed action stored by Python can run after confirmation."
                ),
                warning=str(confirmation_prompt),
            )
        context = getattr(outcome, "context", None)
        if context is not None and not isinstance(context, BrowserActionContext):
            return DispatchResult("SABEL rejected malformed browser context.")
        slots = dict(validated)
        if clarification and "profile" not in slots and "profile_id" not in slots:
            slots["service"] = validated.get("service", "gmail")
        browser_debug = None
        evidence = getattr(outcome, "evidence", None)
        if context is not None:
            browser_debug = {
                "profile": context.profile_id,
                "provider": context.search_engine or context.service,
                "query": context.query,
                "target_connection": context.profile_id,
                "target_tab": getattr(outcome, "target_tab_id", None),
                "generated_url": context.url,
                "result_profile": context.profile_id,
            }
        if evidence is not None:
            browser_debug = {
                **(browser_debug or {}),
                "profile": evidence.requested_profile_id,
                "target_connection": evidence.requested_profile_id,
                "target_tab": evidence.target_tab_id,
                "requested_url": evidence.requested_url,
                "generated_url": evidence.requested_url,
                "request_id": evidence.request_id,
                "result_profile": evidence.result_profile_id,
                "result_url": evidence.result_url,
                "error_code": evidence.error_code,
                "verification": "passed" if evidence.verified else "failed",
            }
        clarification_missing = None
        if clarification:
            if name in {"open_service", "open_service_in_profile"}:
                clarification_missing = ["profile"]
            else:
                clarification_missing = (
                    ["initial_url"]
                    if "service" not in slots and "initial_url" not in slots
                    else ["browser_detail"]
                )
        return DispatchResult(
            message,
            selected_tool=name,
            validated_arguments=validated,
            action_success=bool(getattr(outcome, "success", False)),
            action_state=state_value,
            verified=bool(getattr(outcome, "verified", False)),
            clarification_intent=name if clarification else None,
            clarification_slots=slots if clarification else None,
            clarification_missing=clarification_missing,
            browser_context=context,
            browser_debug=browser_debug,
        )

    def _one_string(self, name: str, arguments: Dict[str, Any], key: str, handler) -> DispatchResult:
        if not _valid_keys(arguments, {key}) or not isinstance(arguments.get(key), str) or not arguments[key].strip():
            return DispatchResult(f"The {name} arguments were rejected.")
        value = arguments[key].strip()
        result = handler(value)
        return DispatchResult(
            result.message,
            selected_tool=name,
            validated_arguments={key: value},
            action_success=result.success,
        )

    def dispatch_pending(
        self, name: str, arguments: Dict[str, Any], user_text: str
    ) -> DispatchResult:
        """Handle only a decision produced by the dedicated pending interpreter."""
        pending = self.state.pending_destructive_action
        if pending is None:
            return DispatchResult("There is no pending action to handle.")
        if name == "cancel_pending_action":
            if arguments:
                return DispatchResult("The cancellation tool accepts no arguments.")
            if pending.tool_name == "confirm_browser_task":
                task_id = pending.arguments.get("task_id")
                outcome = self._browser_call(
                    "cancel_pending_browser_task", task_id
                )
                self.state.clear_pending_destructive_action()
                return self._browser_outcome(
                    "browser_copilot_task",
                    {"task_id": task_id},
                    outcome,
                )
            self.state.clear_pending_destructive_action()
            return DispatchResult("Trash emptying cancelled.", selected_tool=name, validated_arguments={})
        if name == "explain_pending_action":
            if arguments:
                return DispatchResult("The explanation tool accepts no arguments.")
            return DispatchResult(
                pending.warning if pending.tool_name == "confirm_browser_task" else pending.description,
                selected_tool=name,
                validated_arguments={},
            )
        if name == "route_new_request":
            if arguments:
                return DispatchResult("The new-request tool accepts no arguments.")
            if pending.tool_name == "confirm_browser_task":
                self._browser_call(
                    "cancel_pending_browser_task",
                    pending.arguments.get("task_id"),
                )
            self.state.clear_pending_destructive_action()
            return DispatchResult(
                (
                    "Cancelled the pending browser action."
                    if pending.tool_name == "confirm_browser_task"
                    else "Cancelled the pending Trash action."
                ),
                selected_tool=name,
                validated_arguments={},
                continue_with_new_request=True,
            )
        if name != "confirm_pending_action":
            return DispatchResult("SABEL rejected an unknown pending-action tool request.")

        if pending.tool_name == "confirm_browser_task":
            if arguments:
                return DispatchResult(
                    "The confirmation arguments were rejected; the pending browser action was not executed."
                )
            task_id = pending.arguments.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                self.state.clear_pending_destructive_action()
                return DispatchResult("The stored browser action is no longer available.")
            self.state.clear_pending_destructive_action()
            outcome = self._browser_call("resume_browser_task", task_id)
            return self._browser_outcome(
                "browser_copilot_task",
                {"task_id": task_id},
                outcome,
            )

        normalized = normalize_confirmation_arguments(arguments, pending.tool_name)
        if normalized is None:
            return DispatchResult(
                "The confirmation arguments were rejected; the pending action was not executed."
            )
        if not is_explicit_trash_confirmation(user_text):
            return DispatchResult(TRASH_WARNING)

        handler = self.handlers.get(pending.tool_name)
        if handler is None:
            self.state.clear_pending_destructive_action()
            return DispatchResult("The stored pending action is no longer available.")
        try:
            result = handler(**pending.arguments)
        except Exception:
            result = ActionResult(False, "I could not empty Trash because macOS denied the operation.")
        finally:
            self.state.clear_pending_destructive_action()
        return DispatchResult(
            result.message,
            selected_tool=name,
            validated_arguments=normalized,
            action_success=result.success,
        )
