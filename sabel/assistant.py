"""Coordinate typed routing, pending state, actions, and visible responses."""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Callable, Optional

from sabel.cloud_router import CloudRouter
from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.errors import OllamaUnavailableError
from sabel.local_router import (
    ClarificationRequest,
    ClarificationReplyType,
    ClarificationRouter,
    INTERNAL_TOOL_NAMES,
    LocalRouter,
    PendingActionRouter,
    RouterResult,
    RouterResultType,
)
from sabel.ollama_client import OllamaClient
from sabel.openai_research import OpenAIResearchService
from sabel.response_renderer import render_visible_response
from sabel.services import resolve_profile_service
from sabel.tool_dispatcher import DispatchResult, ToolDispatcher


class AssistantResultType(Enum):
    RESPONSE = "response"
    CLARIFICATION = "clarification"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    EXIT = "exit"


@dataclass(frozen=True)
class AssistantResult:
    result_type: AssistantResultType
    message: str
    should_exit: bool = False


CANCEL_WORDS = {
    "cancel",
    "stop",
    "stop action",
    "never mind",
    "forget it",
    "don't do that",
    "do not do that",
}
EXIT_WORDS = {"exit", "quit"}


class SabelAssistant:
    def __init__(
        self,
        settings: Settings,
        ollama_client: Optional[OllamaClient] = None,
        state: Optional[ConversationState] = None,
        local_router: Optional[LocalRouter] = None,
        pending_router: Optional[PendingActionRouter] = None,
        clarification_router: Optional[ClarificationRouter] = None,
        dispatcher: Optional[ToolDispatcher] = None,
        cloud_router: Optional[CloudRouter] = None,
        browser_runtime=None,
    ) -> None:
        self.settings = settings
        self.state = state or ConversationState(
            settings.history_limit,
            settings.pending_action_ttl,
            settings.clarification_ttl,
        )
        self.ollama_client = ollama_client or OllamaClient(settings)
        self.local_router = local_router or LocalRouter(
            self.ollama_client,
            self.state,
            settings.default_music_service,
        )
        self.pending_router = pending_router or PendingActionRouter(
            self.ollama_client, self.state
        )
        self.clarification_router = clarification_router or ClarificationRouter(
            self.ollama_client, self.state
        )
        self.dispatcher = dispatcher or ToolDispatcher(
            settings,
            self.state,
            self.ollama_client.is_available,
            browser_runtime=browser_runtime,
        )
        self.cloud_router = cloud_router or CloudRouter(
            settings,
            self.state,
            lambda: OpenAIResearchService(settings),
        )

    def handle(
        self,
        user_text: str,
        approval_callback: Optional[Callable[[str], str]] = None,
    ) -> AssistantResult:
        cleaned = user_text.strip()
        if not cleaned:
            return AssistantResult(
                AssistantResultType.RESPONSE, "Please type a request."
            )
        normalized = _control_text(cleaned)

        if normalized in EXIT_WORDS:
            self.state.clear_all_pending()
            return self._visible(
                user_text,
                "SABEL is going offline.",
                AssistantResultType.EXIT,
                should_exit=True,
            )

        prefixes = []
        if self.state.clear_expired_pending_destructive_action() is not None:
            prefixes.append("The pending Trash action expired and was cancelled.")
        if self.state.clear_expired_clarification() is not None:
            prefixes.append("The previous clarification expired.")
        if self.state.clear_expired_cloud_fallback() is not None:
            prefixes.append("The previous research fallback choice expired.")

        if self.state.pending_destructive_action is not None:
            return self._handle_pending_destructive(
                user_text, normalized, approval_callback, prefixes
            )

        if self.state.pending_clarification is not None:
            return self._handle_pending_clarification(
                user_text, normalized, approval_callback, prefixes
            )

        if self.state.pending_cloud_fallback is not None:
            return self._handle_pending_cloud_fallback(
                user_text, normalized, approval_callback, prefixes
            )

        if (
            self.state.last_unverified_local_fallback_task
            and _asks_about_unverified_date(cleaned)
        ):
            return self._visible(
                user_text,
                _join_prefixes(
                    prefixes,
                    "I cannot assign a verified current date to that unverified local guidance. "
                    "Use a current browser or cloud research source to establish a date.",
                ),
                AssistantResultType.RESPONSE,
            )

        if normalized in CANCEL_WORDS:
            return self._visible(
                user_text,
                _join_prefixes(prefixes, "There is nothing to cancel."),
                AssistantResultType.RESPONSE,
            )

        return self._handle_normal(
            routing_text=user_text,
            visible_user_text=user_text,
            approval_callback=approval_callback,
            prefixes=prefixes,
        )

    def _handle_pending_cloud_fallback(
        self,
        user_text: str,
        normalized: str,
        approval_callback: Optional[Callable[[str], str]],
        prefixes: list[str],
    ) -> AssistantResult:
        pending = self.state.pending_cloud_fallback
        if pending is None:
            return self._handle_normal(
                user_text, user_text, approval_callback, prefixes
            )
        choice = _cloud_fallback_choice(normalized)
        if choice == "browser":
            self.state.clear_pending_cloud_fallback()
            dispatched = self.dispatcher.dispatch(
                "open_web_search",
                {"query": pending.task, "search_engine": "google"},
                user_text,
            )
            return self._render_dispatch(user_text, dispatched, 0.0, prefixes)
        if choice == "local":
            self.state.clear_pending_cloud_fallback()
            self.state.last_unverified_local_fallback_task = pending.task
            return self._visible(
                user_text,
                _join_prefixes(prefixes, _limited_local_guidance(pending.task)),
                AssistantResultType.RESPONSE,
            )
        if choice == "cancel":
            self.state.clear_pending_cloud_fallback()
            return self._visible(
                user_text,
                _join_prefixes(prefixes, "Research cancelled."),
                AssistantResultType.RESPONSE,
            )
        if _looks_like_fallback_answer(normalized):
            return self._visible(
                user_text,
                _join_prefixes(
                    prefixes,
                    "Please choose 1 for a current browser search, 2 for unverified general guidance, or 3 to cancel.",
                ),
                AssistantResultType.CLARIFICATION,
            )
        self.state.clear_pending_cloud_fallback()
        prefixes.append("Cancelled the previous research choice.")
        return self._handle_normal(
            user_text, user_text, approval_callback, prefixes
        )

    def _handle_pending_destructive(
        self,
        user_text: str,
        normalized: str,
        approval_callback: Optional[Callable[[str], str]],
        prefixes: list[str],
    ) -> AssistantResult:
        if normalized in CANCEL_WORDS:
            dispatched = self.dispatcher.dispatch_pending(
                "cancel_pending_action", {}, user_text
            )
            return self._render_dispatch(user_text, dispatched, 0.0, prefixes)
        try:
            result = self.pending_router.route(user_text)
        except OllamaUnavailableError as error:
            return self._visible(
                user_text,
                _join_prefixes(prefixes, str(error)),
                AssistantResultType.ERROR,
            )
        if result.result_type == RouterResultType.RESPONSE:
            return self._visible(
                user_text,
                _join_prefixes(prefixes, result.message or "Please confirm clearly."),
                AssistantResultType.RESPONSE,
                result.duration_ms,
            )
        if result.result_type != RouterResultType.TOOL_CALLS or len(result.tool_calls) != 1:
            return self._visible(
                user_text,
                _join_prefixes(prefixes, result.message or "I could not safely handle that reply."),
                AssistantResultType.ERROR,
                result.duration_ms,
            )
        call = result.tool_calls[0]
        dispatched = self.dispatcher.dispatch_pending(
            call.name, call.arguments, user_text
        )
        if dispatched.continue_with_new_request:
            prefixes.append(dispatched.message)
            return self._handle_normal(
                routing_text=user_text,
                visible_user_text=user_text,
                approval_callback=approval_callback,
                prefixes=prefixes,
            )
        return self._render_dispatch(
            user_text, dispatched, result.duration_ms, prefixes
        )

    def _handle_pending_clarification(
        self,
        user_text: str,
        normalized: str,
        approval_callback: Optional[Callable[[str], str]],
        prefixes: list[str],
    ) -> AssistantResult:
        pending = self.state.pending_clarification
        if pending is None:
            return self._handle_normal(
                user_text, user_text, approval_callback, prefixes
            )
        if normalized in CANCEL_WORDS:
            self.state.clear_pending_clarification()
            return self._visible(
                user_text,
                _join_prefixes(prefixes, "Cancelled."),
                AssistantResultType.RESPONSE,
            )
        try:
            reply = self.clarification_router.route(user_text)
        except OllamaUnavailableError as error:
            return self._visible(
                user_text,
                _join_prefixes(prefixes, str(error)),
                AssistantResultType.ERROR,
            )

        if reply.reply_type == ClarificationReplyType.CANCELLATION:
            self.state.clear_pending_clarification()
            return self._visible(
                user_text,
                _join_prefixes(prefixes, "Cancelled."),
                AssistantResultType.RESPONSE,
                reply.duration_ms,
            )
        if reply.reply_type == ClarificationReplyType.QUESTION:
            return self._visible(
                user_text,
                _join_prefixes(prefixes, f"I was asking: {pending.question}"),
                AssistantResultType.RESPONSE,
                reply.duration_ms,
            )
        if reply.reply_type == ClarificationReplyType.NEW_REQUEST:
            self.state.clear_pending_clarification()
            prefixes.append("Cancelled the previous request.")
            return self._handle_normal(
                user_text, user_text, approval_callback, prefixes
            )
        if reply.reply_type == ClarificationReplyType.CONFIRMS_SUGGESTION:
            slots = {
                **pending.collected_slots,
                **pending.proposed_slots,
                **reply.supplied_slots,
            }
            return self._resolve_pending_clarification(
                pending.intent,
                slots,
                pending.original_request,
                user_text,
                approval_callback,
                prefixes,
                reply.duration_ms,
            )
        if reply.reply_type == ClarificationReplyType.REJECTS_SUGGESTION:
            slots = {**pending.collected_slots, **reply.supplied_slots}
            if reply.replacement_intent:
                return self._resolve_pending_clarification(
                    reply.replacement_intent,
                    slots,
                    pending.original_request,
                    user_text,
                    approval_callback,
                    prefixes,
                    reply.duration_ms,
                )
            self.state.set_pending_clarification(
                pending.original_request,
                "Okay. Which service should I use?",
                intent=pending.intent,
                collected_slots=pending.collected_slots,
                missing_slots=pending.missing_slots,
            )
            return self._visible(
                user_text,
                _join_prefixes(prefixes, "Okay. Which service should I use?"),
                AssistantResultType.CLARIFICATION,
                reply.duration_ms,
            )
        if reply.reply_type == ClarificationReplyType.SUPPLIES_MISSING_INFORMATION:
            slots = {**pending.collected_slots, **reply.supplied_slots}
            intent = pending.intent
            if intent != "browser_search" and slots.get("service") == "spotify" and "query" in slots:
                intent = "open_spotify_search"
            elif intent != "browser_search" and slots.get("service") == "youtube" and "query" in slots:
                intent = "open_youtube_search"
            if intent:
                return self._resolve_pending_clarification(
                    intent,
                    slots,
                    pending.original_request,
                    user_text,
                    approval_callback,
                    prefixes,
                    reply.duration_ms,
                )
            self.state.clear_pending_clarification()
            combined = (
                f"Original request: {pending.original_request}\n"
                f"Clarification answer: {user_text}"
            )
            return self._handle_normal(
                combined, user_text, approval_callback, prefixes
            )
        return self._visible(
            user_text,
            _join_prefixes(prefixes, reply.message or "Please answer, cancel, or start a new request."),
            AssistantResultType.ERROR,
            reply.duration_ms,
        )

    def _resolve_pending_clarification(
        self,
        intent: str,
        slots: dict[str, object],
        original_request: str,
        user_text: str,
        approval_callback: Optional[Callable[[str], str]],
        prefixes: list[str],
        duration_ms: float,
    ) -> AssistantResult:
        """Execute only an allowlisted intent from Python-owned clarification state."""
        argument_keys = {
            "open_spotify_search": ("query",),
            "open_youtube_search": ("query",),
            "open_application": ("application_name",),
            "open_service": ("service_name",),
            "open_website": ("url",),
            "open_service_in_profile": ("service", "profile"),
        }
        required = argument_keys.get(intent)
        if intent == "browser_search":
            service = slots.get("service")
            query = slots.get("query")
            profile = slots.get("profile")
            if (
                isinstance(service, str)
                and service in {"google", "youtube"}
                and isinstance(query, str)
                and query.strip()
                and (profile is None or profile in {"personal", "nyu"})
            ):
                arguments = {"service": service, "query": query.strip()}
                if isinstance(profile, str):
                    arguments["profile"] = profile
                self.state.clear_pending_clarification()
                dispatched = self.dispatcher.dispatch(
                    intent, arguments, user_text, approval_callback
                )
                return self._render_dispatch(
                    user_text, dispatched, duration_ms, prefixes
                )
        if required and all(isinstance(slots.get(key), str) and slots[key] for key in required):
            arguments = {key: slots[key] for key in required}
            self.state.clear_pending_clarification()
            dispatched = self.dispatcher.dispatch(
                intent, arguments, user_text, approval_callback
            )
            return self._render_dispatch(user_text, dispatched, duration_ms, prefixes)

        self.state.clear_pending_clarification()
        combined = (
            f"Original request: {original_request}\n"
            f"Clarification answer: {user_text}"
        )
        return self._handle_normal(
            combined, user_text, approval_callback, prefixes
        )

    def _handle_normal(
        self,
        routing_text: str,
        visible_user_text: str,
        approval_callback: Optional[Callable[[str], str]],
        prefixes: list[str],
    ) -> AssistantResult:
        try:
            result = self.local_router.route(routing_text)
        except OllamaUnavailableError as error:
            return self._visible(
                visible_user_text,
                _join_prefixes(prefixes, str(error)),
                AssistantResultType.ERROR,
            )

        if result.result_type == RouterResultType.RESPONSE:
            return self._visible(
                visible_user_text,
                _join_prefixes(prefixes, result.message or ""),
                AssistantResultType.RESPONSE,
                result.duration_ms,
            )
        if result.result_type == RouterResultType.CLARIFICATION:
            clarification = result.clarification or ClarificationRequest(
                question=result.message or "What information is missing?",
                missing_slots=[result.expected_slot] if result.expected_slot else [],
            )
            question = clarification.question
            self.state.set_pending_clarification(
                routing_text,
                question,
                intent=clarification.intent,
                collected_slots=clarification.collected_slots,
                missing_slots=clarification.missing_slots,
                proposed_slots=clarification.proposed_slots,
            )
            return self._visible(
                visible_user_text,
                _join_prefixes(prefixes, question),
                AssistantResultType.CLARIFICATION,
                result.duration_ms,
            )
        if result.result_type == RouterResultType.ERROR:
            return self._visible(
                visible_user_text,
                _join_prefixes(prefixes, result.message or "I could not interpret that request."),
                AssistantResultType.ERROR,
                result.duration_ms,
            )
        if len(result.tool_calls) != 1:
            return self._visible(
                visible_user_text,
                _join_prefixes(prefixes, "SABEL rejected an invalid tool result."),
                AssistantResultType.ERROR,
                result.duration_ms,
            )

        call = result.tool_calls[0]
        dispatched = self.dispatcher.dispatch(
            call.name,
            call.arguments,
            visible_user_text,
            approval_callback,
        )
        if result.result_type == RouterResultType.CLOUD_DELEGATION:
            if dispatched.delegation is None:
                return self._render_dispatch(
                    visible_user_text, dispatched, result.duration_ms, prefixes
                )
            cloud = self.cloud_router.handle(dispatched.delegation, approval_callback)
            return self._visible(
                visible_user_text,
                _join_prefixes(prefixes, cloud.message),
                AssistantResultType.TOOL_RESULT,
                result.duration_ms,
                call.name,
                call.arguments,
                cloud.duration_ms,
                cloud.usage,
            )
        return self._render_dispatch(
            visible_user_text, dispatched, result.duration_ms, prefixes
        )

    def _render_dispatch(
        self,
        user_text: str,
        dispatched: DispatchResult,
        local_ms: float,
        prefixes: list[str],
    ) -> AssistantResult:
        message = render_visible_response(dispatched.message, INTERNAL_TOOL_NAMES)
        if dispatched.clarification_intent:
            self.state.set_pending_clarification(
                user_text,
                message,
                intent=dispatched.clarification_intent,
                collected_slots=dispatched.clarification_slots or {},
                missing_slots=dispatched.clarification_missing or [],
            )
            return self._visible(
                user_text,
                _join_prefixes(prefixes, message),
                AssistantResultType.CLARIFICATION,
                local_ms,
                dispatched.selected_tool,
                dispatched.validated_arguments,
            )
        if dispatched.should_exit:
            self.state.clear_all_pending()
        if dispatched.action_success is not None and dispatched.selected_tool:
            self.state.record_action_result(
                dispatched.selected_tool, dispatched.action_success, message
            )
            if dispatched.action_success:
                self._record_browser_context(dispatched)
        result_type = (
            AssistantResultType.EXIT
            if dispatched.should_exit
            else AssistantResultType.TOOL_RESULT
        )
        return self._visible(
            user_text,
            _join_prefixes(prefixes, message),
            result_type,
            local_ms,
            dispatched.selected_tool,
            dispatched.validated_arguments,
            should_exit=dispatched.should_exit,
        )

    def _record_browser_context(self, dispatched: DispatchResult) -> None:
        arguments = dispatched.validated_arguments or {}
        tool = dispatched.selected_tool
        service = None
        profile = None
        query = None
        if tool == "browser_search":
            service = arguments.get("service")
            profile = arguments.get("profile") or "personal"
            query = arguments.get("query")
        elif tool == "open_service_in_profile":
            resolution = resolve_profile_service(str(arguments.get("service") or ""))
            service = resolution.service_name
            profile = arguments.get("profile") or (
                resolution.service.default_profile if resolution.service else None
            )
        elif tool == "open_website":
            address = str(arguments.get("url") or "").casefold()
            if "google.com" in address:
                service = "google"
            elif "youtube.com" in address:
                service = "youtube"
        elif tool == "open_youtube_search":
            service = "youtube"
            query = arguments.get("query")
        if service in {"google", "youtube"}:
            self.state.record_browser_context(
                service,
                profile if profile in {"personal", "nyu"} else None,
                str(query) if isinstance(query, str) else None,
            )

    def _visible(
        self,
        user_text: str,
        message: str,
        result_type: AssistantResultType,
        local_ms: float = 0.0,
        tool: Optional[str] = None,
        arguments=None,
        cloud_ms: float = 0.0,
        usage=None,
        should_exit: bool = False,
    ) -> AssistantResult:
        safe_message = render_visible_response(message, INTERNAL_TOOL_NAMES)
        self.state.add_exchange(user_text, safe_message)
        return AssistantResult(
            result_type,
            self._debug(
                safe_message, local_ms, tool, arguments, cloud_ms, usage
            ),
            should_exit,
        )

    def _debug(
        self,
        message: str,
        local_ms: float,
        tool: Optional[str],
        arguments,
        cloud_ms: float = 0.0,
        usage=None,
    ) -> str:
        if not self.settings.debug:
            return message
        lines = [
            f"[debug] Local model: {self.settings.ollama_model}",
            f"[debug] Local inference: {local_ms:.1f} ms",
            f"[debug] Cloud mode: {self.settings.cloud_mode}",
        ]
        if cloud_ms:
            lines.extend(
                [
                    f"[debug] OpenAI model: {self.settings.openai_model}",
                    f"[debug] Cloud request: {cloud_ms:.1f} ms",
                    f"[debug] Token usage: {usage or 'not reported'}",
                ]
            )
        return "\n".join(lines + [message])


def _control_text(text: str) -> str:
    return " ".join(text.casefold().strip().rstrip(".!?").split())


def _cloud_fallback_choice(normalized: str) -> Optional[str]:
    compact = normalized.strip()
    if compact in {
        "1", "one", "option 1", "browser", "browser search", "open a browser search"
    }:
        return "browser"
    if compact in {
        "2", "two", "option 2", "local", "local answer", "general guidance",
        "limited local answer",
    }:
        return "local"
    if compact in {"3", "three", "option 3", "cancel", "never mind", "stop"}:
        return "cancel"
    return None


def _looks_like_fallback_answer(normalized: str) -> bool:
    return bool(
        normalized.isdigit()
        or re.search(
            r"\b(?:option|choice|browser|local|guidance|cancel)\b", normalized
        )
    )


def _limited_local_guidance(task: str) -> str:
    heading = "Local fallback — current web information was not verified"
    if re.search(r"\blaptops?\b", task, re.I):
        return (
            f"{heading}\n\n"
            "I cannot provide a verified current ranking, price, or availability list. "
            "For a useful laptop comparison, start with your budget and operating-system needs, then compare: "
            "processor class, memory, storage, display, battery life, ports, repairability, warranty, and the return policy. "
            "For demanding creative or development work, prioritize sustained performance and memory; "
            "for travel, prioritize weight, battery life, and charging compatibility."
        )
    return (
        f"{heading}\n\n"
        "I cannot provide a verified current ranking, price, availability, date, or citation. "
        "A safe general approach is to define your requirements and budget, compare measurable tradeoffs, "
        "and verify the final choice against current primary sources before acting."
    )


def _asks_about_unverified_date(text: str) -> bool:
    folded = text.casefold()
    return bool(
        re.search(r"\b(?:what|which|around what|how current).{0,30}\bdate\b", folded)
        or re.search(r"\b(?:recommendations?|guidance).{0,30}\bvalid\b", folded)
    )


def _join_prefixes(prefixes: list[str], message: str) -> str:
    return " ".join([*prefixes, message]).strip()
