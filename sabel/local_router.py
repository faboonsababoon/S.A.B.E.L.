"""Typed Ollama routing for normal, destructive, and clarification modes."""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import List, Optional

from sabel.actions import application_mentioned
from sabel.browser_protocol import ACTION_FIELDS
from sabel.confirmations import (
    TRASH_WARNING,
    UNCERTAIN_PENDING_RESPONSE,
    classify_pending_response,
)
from sabel.conversation_state import ConversationState
from sabel.ollama_client import LocalToolCall, OllamaClient, OllamaResponse
from sabel.media import parse_media_request
from sabel.services import service_mentioned
from sabel.tool_schemas import (
    LOCAL_SYSTEM_PROMPT,
    LOCAL_TOOLS,
    PENDING_CLARIFICATION_SYSTEM_PROMPT,
    PENDING_CLARIFICATION_TOOLS,
    PENDING_CONFIRMATION_TOOLS,
    PENDING_SYSTEM_PROMPT,
)


class RouterResultType(Enum):
    RESPONSE = "response"
    TOOL_CALLS = "tool_calls"
    CLARIFICATION = "clarification"
    CLOUD_DELEGATION = "cloud_delegation"
    ERROR = "error"


@dataclass(frozen=True)
class ClarificationRequest:
    question: str
    intent: str = ""
    collected_slots: dict[str, object] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    proposed_slots: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CloudDelegationRequest:
    task: str
    reason: str
    requires_current_web_information: bool


@dataclass(frozen=True)
class RouterResult:
    result_type: RouterResultType
    message: Optional[str] = None
    tool_calls: List[LocalToolCall] = field(default_factory=list)
    clarification: Optional[ClarificationRequest] = None
    cloud_request: Optional[CloudDelegationRequest] = None
    expected_slot: Optional[str] = None
    duration_ms: float = 0.0
    error_code: Optional[str] = None


class ClarificationReplyType(Enum):
    SUPPLIES_MISSING_INFORMATION = "supplies_missing_information"
    CONFIRMS_SUGGESTION = "confirms_suggested_interpretation"
    REJECTS_SUGGESTION = "rejects_suggested_interpretation"
    CANCELLATION = "cancellation"
    QUESTION = "question"
    NEW_REQUEST = "new_request"
    ERROR = "error"
    ANSWER = "supplies_missing_information"


@dataclass(frozen=True)
class ClarificationReply:
    reply_type: ClarificationReplyType
    duration_ms: float = 0.0
    message: Optional[str] = None
    supplied_slots: dict[str, object] = field(default_factory=dict)
    replacement_intent: Optional[str] = None


INTERNAL_TOOL_NAMES = {
    tool["function"]["name"]
    for tool in LOCAL_TOOLS + PENDING_CONFIRMATION_TOOLS + PENDING_CLARIFICATION_TOOLS
}
INTERNAL_TOOL_NAMES.update(ACTION_FIELDS)


class LocalRouter:
    def __init__(
        self,
        client: OllamaClient,
        state: ConversationState,
        default_music_service: Optional[str] = None,
    ) -> None:
        self.client = client
        self.state = state
        self.default_music_service = default_music_service

    def route(self, user_text: str) -> RouterResult:
        messages = [{"role": "system", "content": LOCAL_SYSTEM_PROMPT}]
        messages.extend(self.state.messages())
        messages.extend(
            [
                {"role": "system", "content": self.state.research_context()},
                {"role": "system", "content": self.state.action_context()},
                {
                    "role": "system",
                    "content": "No clarification is pending. Treat the latest user message independently.",
                },
                {"role": "user", "content": user_text},
            ]
        )
        active_tools = _normal_tools_for(user_text, self.state)
        response = self.client.chat(messages, active_tools)
        result = _normal_result(response)
        if result.error_code == "raw_tool_name":
            corrective_messages = [
                *messages,
                {"role": "assistant", "content": response.content},
                {
                    "role": "system",
                    "content": (
                        "Your previous output was an internal tool reference in prose. "
                        "Return the matching native tool call with schema-valid arguments, "
                        "or return a normal conversational answer. Never print a tool name."
                    ),
                },
            ]
            corrected = self.client.chat(corrective_messages, active_tools)
            result = _normal_result(corrected)
        return _safe_domain_fallback(
            user_text,
            result,
            default_music_service=self.default_music_service,
            state=self.state,
        )


class PendingActionRouter:
    """Interpret one reply with destructive-action context before normal routing."""

    def __init__(self, client: OllamaClient, state: ConversationState) -> None:
        self.client = client
        self.state = state

    def route(self, user_text: str) -> RouterResult:
        pending = self.state.pending_destructive_action
        if pending is None:
            return RouterResult(
                RouterResultType.ERROR,
                message="There is no pending destructive action.",
            )
        messages = [{"role": "system", "content": PENDING_SYSTEM_PROMPT}]
        messages.extend(self.state.messages())
        messages.append(
            {
                "role": "system",
                "content": (
                    f"Pending tool: {pending.tool_name}\n"
                    f"Description: {pending.description}\n"
                    f"Warning: {pending.warning}\n"
                    "Python owns the stored arguments; never regenerate them."
                ),
            }
        )
        messages.append({"role": "user", "content": user_text})
        response = self.client.chat(messages, PENDING_CONFIRMATION_TOOLS)
        result = _tool_result(response)
        deterministic_intent = classify_pending_response(user_text)
        if deterministic_intent == UNCERTAIN_PENDING_RESPONSE:
            return RouterResult(
                RouterResultType.RESPONSE,
                message=TRASH_WARNING,
                duration_ms=response.duration_ms,
            )
        if deterministic_intent is not None:
            arguments = {}
            if result.tool_calls and result.tool_calls[0].name == deterministic_intent:
                arguments = result.tool_calls[0].arguments
            return RouterResult(
                RouterResultType.TOOL_CALLS,
                tool_calls=[LocalToolCall(deterministic_intent, arguments)],
                duration_ms=response.duration_ms,
            )
        if result.result_type == RouterResultType.TOOL_CALLS:
            return result
        return RouterResult(
            RouterResultType.TOOL_CALLS,
            tool_calls=[LocalToolCall("route_new_request", {})],
            duration_ms=response.duration_ms,
        )


class ClarificationRouter:
    """Classify one reply using only four compact clarification tools."""

    TOOL_TO_REPLY = {
        "answer_clarification": ClarificationReplyType.ANSWER,
        "cancel_clarification": ClarificationReplyType.CANCELLATION,
        "question_about_clarification": ClarificationReplyType.QUESTION,
        "route_new_request": ClarificationReplyType.NEW_REQUEST,
    }

    def __init__(self, client: OllamaClient, state: ConversationState) -> None:
        self.client = client
        self.state = state

    def route(self, user_text: str) -> ClarificationReply:
        pending = self.state.pending_clarification
        if pending is None:
            return ClarificationReply(
                ClarificationReplyType.ERROR,
                message="There is no pending clarification.",
            )
        deterministic = _classify_clarification_reply(user_text, pending)
        if deterministic is not None:
            return deterministic

        messages = [
            {"role": "system", "content": PENDING_CLARIFICATION_SYSTEM_PROMPT}
        ]
        messages.extend(self.state.messages())
        messages.extend(
            [
                {
                    "role": "system",
                    "content": (
                        f"Original request: {pending.original_request}\n"
                        f"Clarification question: {pending.question}\n"
                        f"Intent: {pending.intent or 'unspecified'}\n"
                        f"Collected slots: {pending.collected_slots}\n"
                        f"Missing slots: {pending.missing_slots}\n"
                        f"Proposed slots: {pending.proposed_slots}"
                    ),
                },
                {"role": "user", "content": user_text},
            ]
        )
        response = self.client.chat(messages, PENDING_CLARIFICATION_TOOLS)
        if len(response.tool_calls) != 1:
            return ClarificationReply(
                ClarificationReplyType.ERROR,
                duration_ms=response.duration_ms,
                message="I could not safely interpret that clarification reply.",
            )
        call = response.tool_calls[0]
        reply_type = self.TOOL_TO_REPLY.get(call.name)
        if reply_type is None or call.arguments:
            return ClarificationReply(
                ClarificationReplyType.ERROR,
                duration_ms=response.duration_ms,
                message="I could not safely interpret that clarification reply.",
            )
        return ClarificationReply(reply_type, duration_ms=response.duration_ms)


def _normal_result(response: OllamaResponse) -> RouterResult:
    if len(response.tool_calls) > 1:
        return RouterResult(
            RouterResultType.ERROR,
            message="SABEL rejected multiple tool requests. Please ask for one action.",
            duration_ms=response.duration_ms,
        )
    if not response.tool_calls:
        content = response.content.strip()
        if not content:
            return RouterResult(
                RouterResultType.ERROR,
                message="I could not interpret that request. Please try rephrasing it.",
                duration_ms=response.duration_ms,
            )
        if _contains_internal_reference(content):
            return RouterResult(
                RouterResultType.ERROR,
                message="I could not safely interpret that request. Please try rephrasing it.",
                duration_ms=response.duration_ms,
                error_code="raw_tool_name",
            )
        return RouterResult(
            RouterResultType.RESPONSE,
            message=content,
            duration_ms=response.duration_ms,
        )

    call = response.tool_calls[0]
    if call.name == "request_clarification":
        arguments = call.arguments
        allowed = {"question", "expected_slot"}
        question = arguments.get("question") if isinstance(arguments, dict) else None
        expected_slot = (
            arguments.get("expected_slot") if isinstance(arguments, dict) else None
        )
        if (
            not isinstance(arguments, dict)
            or not set(arguments).issubset(allowed)
            or not isinstance(question, str)
            or not question.strip()
            or (expected_slot is not None and not isinstance(expected_slot, str))
        ):
            return RouterResult(
                RouterResultType.ERROR,
                message="I need more information, but the clarification was malformed.",
                duration_ms=response.duration_ms,
            )
        return RouterResult(
            RouterResultType.CLARIFICATION,
            message=question.strip(),
            clarification=ClarificationRequest(
                question=question.strip(),
                missing_slots=[expected_slot.strip()] if expected_slot else [],
            ),
            expected_slot=expected_slot.strip() if expected_slot else None,
            duration_ms=response.duration_ms,
        )

    if call.name == "delegate_to_openai":
        arguments = call.arguments
        required = {"task", "reason", "requires_current_web_information"}
        if (
            not isinstance(arguments, dict)
            or set(arguments) != required
            or not isinstance(arguments.get("task"), str)
            or not arguments["task"].strip()
            or not isinstance(arguments.get("reason"), str)
            or not isinstance(arguments.get("requires_current_web_information"), bool)
        ):
            return RouterResult(
                RouterResultType.ERROR,
                message="The cloud research request was malformed.",
                duration_ms=response.duration_ms,
            )
        cloud_request = CloudDelegationRequest(
            task=arguments["task"].strip(),
            reason=arguments["reason"].strip(),
            requires_current_web_information=arguments[
                "requires_current_web_information"
            ],
        )
        return RouterResult(
            RouterResultType.CLOUD_DELEGATION,
            tool_calls=[call],
            cloud_request=cloud_request,
            duration_ms=response.duration_ms,
        )

    return RouterResult(
        RouterResultType.TOOL_CALLS,
        tool_calls=[call],
        duration_ms=response.duration_ms,
    )


def _tool_result(response: OllamaResponse) -> RouterResult:
    if len(response.tool_calls) != 1:
        return RouterResult(
            RouterResultType.ERROR,
            message="I could not safely interpret that pending-action reply.",
            duration_ms=response.duration_ms,
        )
    return RouterResult(
        RouterResultType.TOOL_CALLS,
        tool_calls=[response.tool_calls[0]],
        duration_ms=response.duration_ms,
    )


def _normal_tools_for(
    user_text: str, state: Optional[ConversationState] = None
) -> list[dict]:
    """Reduce choices by domain without selecting the final action."""
    lowered = user_text.casefold()
    profile_service = _profile_service_slots(user_text)
    if _looks_like_browser_tabs(lowered):
        names = {"show_browser_tabs", "request_clarification"}
    elif _looks_like_browser_profile_request(lowered):
        names = {"show_browser_profiles"}
    elif _looks_like_browser_stop(lowered):
        names = {"stop_browser_task"}
    elif _looks_like_browser_audit(lowered):
        names = {"show_recent_browser_actions"}
    elif _youtube_channel_target(user_text):
        names = {"browser_copilot_task", "request_clarification"}
    elif _parse_browser_search_request(user_text, state) is not None:
        names = {"browser_search", "request_clarification"}
    elif _looks_like_explicit_url_request(lowered):
        names = {"open_website", "request_clarification"}
    elif (
        profile_service
        and _looks_like_navigation_request(lowered)
        and _is_profile_service_homepage_request(
            lowered, profile_service[0]
        )
    ):
        names = {"open_service_in_profile", "request_clarification"}
    elif "trash" in lowered:
        names = {"empty_trash", "get_trash_status", "request_clarification"}
    elif lowered.strip().startswith("play "):
        names = {
            "open_spotify_search",
            "open_youtube_search",
            "request_clarification",
        }
    elif _looks_like_status_request(lowered):
        names = {"show_status"}
    elif service_mentioned(lowered) and _looks_like_navigation_request(lowered):
        names = {"open_service", "open_youtube_search", "request_clarification"}
    elif application_mentioned(lowered) and _looks_like_application_request(lowered):
        names = {"open_application", "request_clarification"}
    elif _looks_like_research_request(lowered):
        names = {"delegate_to_openai", "open_web_search", "request_clarification"}
    else:
        return LOCAL_TOOLS
    return [tool for tool in LOCAL_TOOLS if tool["function"]["name"] in names]


def _safe_domain_fallback(
    user_text: str,
    result: RouterResult,
    *,
    default_music_service: Optional[str] = None,
    state: Optional[ConversationState] = None,
) -> RouterResult:
    """Recover typed outcomes from the original request, never from model prose."""
    lowered = " ".join(user_text.casefold().split())
    service_match = service_mentioned(user_text)

    media = parse_media_request(user_text)
    if media is not None:
        if not media.query:
            question = "What would you like me to search for?"
            return RouterResult(
                RouterResultType.CLARIFICATION,
                message=question,
                clarification=ClarificationRequest(
                    question=question,
                    intent="media_search",
                    missing_slots=["query"],
                ),
                expected_slot="query",
                duration_ms=result.duration_ms,
            )
        selected_service = media.service or default_music_service
        if selected_service in {"spotify", "youtube"}:
            tool_name = (
                "open_spotify_search"
                if selected_service == "spotify"
                else "open_youtube_search"
            )
            return RouterResult(
                RouterResultType.TOOL_CALLS,
                tool_calls=[LocalToolCall(tool_name, {"query": media.query})],
                duration_ms=result.duration_ms,
            )
        return _media_service_clarification(media.query, result.duration_ms)
    if re.search(r"\b(?:press|hit|resume)\s+play\b", lowered):
        return RouterResult(
            RouterResultType.RESPONSE,
            message=(
                "I cannot verify playback control yet. I can open Spotify or YouTube "
                "search results for a track instead."
            ),
            duration_ms=result.duration_ms,
        )

    if _looks_like_browser_tabs(lowered):
        profile = _profile_from_text(lowered)
        if profile:
            return RouterResult(
                RouterResultType.TOOL_CALLS,
                tool_calls=[LocalToolCall("show_browser_tabs", {"profile": profile})],
                duration_ms=result.duration_ms,
            )
    if _looks_like_browser_profile_request(lowered):
        return _zero_argument_result("show_browser_profiles", result.duration_ms)
    if _looks_like_browser_stop(lowered):
        return _zero_argument_result("stop_browser_task", result.duration_ms)
    if _looks_like_browser_audit(lowered):
        return _zero_argument_result("show_recent_browser_actions", result.duration_ms)
    explicit_url = _extract_explicit_url(user_text)
    if explicit_url and _looks_like_navigation_request(lowered):
        return RouterResult(
            RouterResultType.TOOL_CALLS,
            tool_calls=[LocalToolCall("open_website", {"url": explicit_url})],
            duration_ms=result.duration_ms,
        )
    channel_target = _youtube_channel_target(user_text)
    if channel_target:
        return RouterResult(
            RouterResultType.TOOL_CALLS,
            tool_calls=[
                LocalToolCall(
                    "browser_copilot_task",
                    {
                        "objective": user_text.strip(),
                        "service": "youtube",
                        "profile": "personal",
                    },
                )
            ],
            duration_ms=result.duration_ms,
        )
    search_request = _parse_browser_search_request(user_text, state)
    if search_request and search_request.get("service"):
        search_slots = {
            key: value
            for key, value in search_request.items()
            if key in {"service", "query", "profile"} and value
        }
        return RouterResult(
            RouterResultType.TOOL_CALLS,
            tool_calls=[LocalToolCall("browser_search", search_slots)],
            duration_ms=result.duration_ms,
        )
    if search_request:
        query = str(search_request.get("query") or "").strip()
        if not query:
            question = "What would you like me to search for?"
            missing = ["query"]
        else:
            question = f"Should I search Google or YouTube for “{query}”?"
            missing = ["service"]
        collected = {
            key: value
            for key, value in search_request.items()
            if key in {"query", "profile"} and value
        }
        return RouterResult(
            RouterResultType.CLARIFICATION,
            message=question,
            clarification=ClarificationRequest(
                question=question,
                intent="browser_search",
                collected_slots=collected,
                missing_slots=missing,
            ),
            expected_slot=missing[0],
            duration_ms=result.duration_ms,
        )
    profile_service = _profile_service_slots(user_text)
    if (
        profile_service
        and _looks_like_navigation_request(lowered)
        and _is_profile_service_homepage_request(lowered, profile_service[0])
    ):
        arguments: dict[str, object] = {"service": profile_service[0]}
        if profile_service[1]:
            arguments["profile"] = profile_service[1]
        return RouterResult(
            RouterResultType.TOOL_CALLS,
            tool_calls=[LocalToolCall("open_service_in_profile", arguments)],
            duration_ms=result.duration_ms,
        )

    if result.result_type == RouterResultType.CLOUD_DELEGATION:
        return result
    if result.result_type == RouterResultType.TOOL_CALLS:
        if service_match and _is_service_homepage_request(lowered, service_match[0]):
            return _service_result(service_match[0], result.duration_ms)
        if _looks_like_research_request(lowered):
            return _cloud_result_for(user_text, result.duration_ms)
        explicit_url = _extract_explicit_url(user_text)
        if explicit_url and _looks_like_navigation_request(lowered):
            return RouterResult(
                RouterResultType.TOOL_CALLS,
                tool_calls=[LocalToolCall("open_website", {"url": explicit_url})],
                duration_ms=result.duration_ms,
            )
        application_name = application_mentioned(user_text)
        if application_name and _looks_like_application_request(lowered):
            call = result.tool_calls[0] if result.tool_calls else None
            if call is None or call.name != "open_application":
                return _application_result(application_name, result.duration_ms)
        return result
    if result.result_type == RouterResultType.CLARIFICATION:
        return result
    if "trash" in lowered:
        inspection_markers = (
            "is my",
            "how many",
            "do i have",
            "anything in",
            "contains",
            "check",
            "inspect",
        )
        destructive_markers = ("clear", "empty", "delete", "remove", "clean")
        if any(marker in lowered for marker in inspection_markers):
            return RouterResult(
                RouterResultType.TOOL_CALLS,
                tool_calls=[LocalToolCall("get_trash_status", {})],
                duration_ms=result.duration_ms,
            )
        if any(marker in lowered for marker in destructive_markers):
            return RouterResult(
                RouterResultType.TOOL_CALLS,
                tool_calls=[LocalToolCall("empty_trash", {})],
                duration_ms=result.duration_ms,
            )
    if _looks_like_status_request(lowered):
        return RouterResult(
            RouterResultType.TOOL_CALLS,
            tool_calls=[LocalToolCall("show_status", {})],
            duration_ms=result.duration_ms,
        )

    application_name = application_mentioned(user_text)
    if application_name and _looks_like_application_request(lowered):
        return _application_result(application_name, result.duration_ms)

    if service_match and _looks_like_navigation_request(lowered):
        return _service_result(service_match[0], result.duration_ms)

    explicit_url = _extract_explicit_url(user_text)
    if explicit_url and _looks_like_navigation_request(lowered):
        return RouterResult(
            RouterResultType.TOOL_CALLS,
            tool_calls=[LocalToolCall("open_website", {"url": explicit_url})],
            duration_ms=result.duration_ms,
        )

    if _looks_like_research_request(lowered):
        return _cloud_result_for(user_text, result.duration_ms)

    return result


def _media_service_clarification(target: str, duration_ms: float) -> RouterResult:
    display = target[:1].upper() + target[1:]
    question = f'Which service should I use for “{display}”: Spotify or YouTube?'
    clarification = ClarificationRequest(
        question=question,
        intent="media_search",
        collected_slots={"query": target},
        missing_slots=["service"],
    )
    return RouterResult(
        RouterResultType.CLARIFICATION,
        message=question,
        clarification=clarification,
        expected_slot="service",
        duration_ms=duration_ms,
    )


def _application_result(application_name: str, duration_ms: float) -> RouterResult:
    return RouterResult(
        RouterResultType.TOOL_CALLS,
        tool_calls=[
            LocalToolCall("open_application", {"application_name": application_name})
        ],
        duration_ms=duration_ms,
    )


def _zero_argument_result(name: str, duration_ms: float) -> RouterResult:
    return RouterResult(
        RouterResultType.TOOL_CALLS,
        tool_calls=[LocalToolCall(name, {})],
        duration_ms=duration_ms,
    )


def _service_result(service_name: str, duration_ms: float) -> RouterResult:
    return RouterResult(
        RouterResultType.TOOL_CALLS,
        tool_calls=[LocalToolCall("open_service", {"service_name": service_name})],
        duration_ms=duration_ms,
    )


def _cloud_result_for(user_text: str, duration_ms: float) -> RouterResult:
    lowered = user_text.casefold()
    current = any(
        marker in lowered
        for marker in ("current", "latest", "right now", "today", "market")
    )
    cloud_request = CloudDelegationRequest(
        task=user_text.strip(),
        reason="research requiring synthesis",
        requires_current_web_information=current,
    )
    call = LocalToolCall(
        "delegate_to_openai",
        {
            "task": cloud_request.task,
            "reason": cloud_request.reason,
            "requires_current_web_information": current,
        },
    )
    return RouterResult(
        RouterResultType.CLOUD_DELEGATION,
        tool_calls=[call],
        cloud_request=cloud_request,
        duration_ms=duration_ms,
    )


def _contains_internal_reference(content: str) -> bool:
    folded = content.casefold()
    return any(
        re.search(rf"(?<![a-z0-9_]){re.escape(name.casefold())}(?![a-z0-9_])", folded)
        for name in INTERNAL_TOOL_NAMES
    )


def _looks_like_status_request(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    return "status" in words or (
        bool(words & {"working", "running", "operating"})
        and bool(words & {"normally", "normal", "okay", "ok"})
    )


def _looks_like_navigation_request(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    return bool(words & {"open", "go", "visit", "take", "pull", "navigate"})


def _looks_like_browser_profile_request(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    return (
        bool(words & {"profile", "profiles"})
        and bool(words & {"browser", "chrome"})
        and bool(words & {"show", "list", "connected", "status"})
    )


def _looks_like_browser_tabs(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    return bool(words & {"tab", "tabs"}) and "browser" in words and bool(
        words & {"show", "list", "open"}
    )


def _profile_from_text(text: str) -> Optional[str]:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    if "personal" in words:
        return "personal"
    if "nyu" in words:
        return "nyu"
    return None


def _looks_like_browser_stop(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    return bool(words & {"stop", "cancel"}) and bool(
        words & {"browser", "browsing"}
    )


def _looks_like_browser_audit(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    return "browser" in words and "actions" in words and bool(
        words & {"show", "recent", "history", "audit"}
    )


def _youtube_channel_target(text: str) -> Optional[str]:
    cleaned = " ".join(text.strip().split()).strip(" .!?")
    match = re.match(
        r"^(?:(?:open|go to|take me to|navigate to|pull up)\s+)?(.+?)(?:['’]s)?\s+youtube\s+channel$",
        cleaned,
        re.I,
    )
    if not match:
        return None
    target = match.group(1).strip()
    return target if target else None


def _parse_browser_search_request(
    text: str, state: Optional[ConversationState] = None
) -> Optional[dict[str, object]]:
    """Parse common search word order while preserving Python-owned context."""
    cleaned = " ".join(text.strip().split()).strip(" .!?")
    match = re.match(
        r"^(?:please\s+)?(?:search(?:\s+up)?|look\s+up|find)\s*(.*)$",
        cleaned,
        re.I,
    )
    if not match:
        return None
    remainder = match.group(1).strip()
    profile = None
    profile_match = re.search(
        r"\s+in\s+(personal|nyu)(?:\s+(?:profile|browser))?$",
        remainder,
        re.I,
    )
    if profile_match:
        profile = profile_match.group(1).casefold()
        remainder = remainder[: profile_match.start()].strip()
    remainder = re.sub(r"\s+(?:in|on)\s+it$", "", remainder, flags=re.I).strip()

    service = None
    service_first = re.match(
        r"^(google|youtube)(?:\.com)?(?:\s+for)?\s+(.+)$",
        remainder,
        re.I,
    )
    service_last = re.match(
        r"^(.+?)\s+(?:on|in)\s+(google|youtube)(?:\.com)?$",
        remainder,
        re.I,
    )
    if service_first:
        service = service_first.group(1).casefold()
        remainder = service_first.group(2)
    elif service_last:
        remainder = service_last.group(1)
        service = service_last.group(2).casefold()

    query = remainder.strip().removesuffix(" up").strip()
    if len(query) >= 2 and (
        (query[0] == query[-1] and query[0] in {'"', "'"})
        or (query[0], query[-1]) in {("“", "”"), ("‘", "’")}
    ):
        query = query[1:-1].strip()
    if re.fullmatch(
        r"(?:that\s+)?(?:same\s+)?(?:string|search|query|thing|one|it)",
        query,
        re.I,
    ):
        query = state.last_browser_query if state and state.last_browser_query else query
    if service is None and state is not None:
        if state.last_browser_service in {"google", "youtube"}:
            service = state.last_browser_service
    if profile is None and state is not None:
        profile = state.last_browser_profile

    values: dict[str, object] = {"query": query}
    if service:
        values["service"] = service
    if profile:
        values["profile"] = profile
    return values


def _browser_search_slots(text: str) -> Optional[dict[str, object]]:
    """Compatibility wrapper for callers that do not have session context."""
    parsed = _parse_browser_search_request(text)
    return parsed if parsed and parsed.get("service") else None


def _profile_service_slots(text: str) -> Optional[tuple[str, Optional[str]]]:
    folded = text.casefold()
    explicit_profile = None
    if re.search(r"(?<![a-z0-9])personal(?![a-z0-9])", folded):
        explicit_profile = "personal"
    elif re.search(r"(?<![a-z0-9])nyu(?![a-z0-9])", folded):
        explicit_profile = "nyu"
    if re.search(r"\bpersonal\s+gmail\b", folded):
        return "personal_gmail", "personal"
    if re.search(r"\bnyu\s+gmail\b", folded):
        return "nyu_gmail", "nyu"
    if re.search(r"(?<![a-z0-9])gmail(?![a-z0-9])", folded):
        return "gmail", explicit_profile
    mentioned = service_mentioned(text)
    if mentioned:
        service_name, service = mentioned
        return service_name, explicit_profile or service.default_profile
    return None


def _is_service_homepage_request(text: str, service_name: str) -> bool:
    """Distinguish a bare service destination from content search on that service."""
    words = re.findall(r"[a-z0-9]+", text.casefold())
    framing = {
        "a",
        "can",
        "could",
        "go",
        "home",
        "homepage",
        "me",
        "navigate",
        "open",
        "page",
        "please",
        "pull",
        "service",
        "take",
        "the",
        "to",
        "up",
        "visit",
        "website",
        "would",
        "you",
        service_name.casefold(),
    }
    return _looks_like_navigation_request(text) and not [
        word for word in words if word not in framing
    ]


def _is_profile_service_homepage_request(text: str, service_name: str) -> bool:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    framing = {
        "a", "albert", "browser", "can", "could", "gmail", "github", "go",
        "google", "home", "homepage", "in", "me", "navigate", "nyu", "open",
        "page", "personal", "please", "profile", "pull", "service", "take",
        "the", "to", "up", "visit", "website", "would", "you", "youtube",
        service_name.casefold(),
    }
    return _looks_like_navigation_request(text) and not [
        word for word in words if word not in framing
    ]


def _looks_like_application_request(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    return bool(words & {"open", "launch", "start", "run"})


def _looks_like_research_request(text: str) -> bool:
    words = set(re.findall(r"[a-z]+", text.casefold()))
    return bool(words & {"research", "compare", "recommend", "investigate"})


def _looks_like_explicit_url_request(text: str) -> bool:
    return _extract_explicit_url(text) is not None


def _extract_explicit_url(text: str) -> Optional[str]:
    match = re.search(
        r"(?i)\b(?:https?://)?(?:www\.)?[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}(?:/[^\s]*)?",
        text,
    )
    return match.group(0).rstrip(".,!?;:") if match else None


def _classify_clarification_reply(user_text: str, pending) -> Optional[ClarificationReply]:
    """Resolve common safe replies from authoritative pending context."""
    normalized = " ".join(user_text.casefold().strip().rstrip(".!?").split())
    words = set(re.findall(r"[a-z]+", normalized))
    if normalized in {"cancel", "stop", "never mind", "forget it"}:
        return ClarificationReply(ClarificationReplyType.CANCELLATION)
    if user_text.strip().endswith("?") or words & {"why", "what"}:
        return ClarificationReply(ClarificationReplyType.QUESTION)

    supplied: dict[str, object] = {}
    if re.search(r"(?<![a-z0-9])personal(?![a-z0-9])", normalized):
        supplied["profile"] = "personal"
    elif re.search(r"(?<![a-z0-9])nyu(?![a-z0-9])", normalized):
        supplied["profile"] = "nyu"
    service_match = service_mentioned(user_text)
    if service_match:
        supplied["service"] = service_match[0]
    elif re.search(r"(?<![a-z0-9])spotify(?![a-z0-9])", normalized):
        supplied["service"] = "spotify"
    query = pending.collected_slots.get("query")
    if isinstance(query, str) and query.casefold() in normalized:
        supplied["query"] = query

    affirmation = bool(words & {"yes", "yeah", "yep", "correct", "right"}) or normalized in {
        "that's right",
        "that is right",
    }
    rejection = bool(words & {"no", "nope"})
    if rejection:
        replacement = None
        if supplied.get("service") == "youtube" and isinstance(query, str):
            replacement = "open_youtube_search"
        return ClarificationReply(
            ClarificationReplyType.REJECTS_SUGGESTION,
            supplied_slots=supplied,
            replacement_intent=replacement,
        )
    if affirmation:
        return ClarificationReply(
            ClarificationReplyType.CONFIRMS_SUGGESTION,
            supplied_slots=supplied,
        )
    if supplied:
        return ClarificationReply(
            ClarificationReplyType.SUPPLIES_MISSING_INFORMATION,
            supplied_slots=supplied,
        )

    if _looks_like_navigation_request(normalized) or _looks_like_application_request(normalized):
        return ClarificationReply(ClarificationReplyType.NEW_REQUEST)
    return None
