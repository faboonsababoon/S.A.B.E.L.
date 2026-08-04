"""Ask Ollama to select one approved local tool or request clarification."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from sabel.confirmations import (
    TRASH_WARNING,
    UNCERTAIN_PENDING_RESPONSE,
    classify_pending_response,
)
from sabel.conversation_state import ConversationState
from sabel.ollama_client import OllamaClient
from sabel.tool_schemas import (
    LOCAL_SYSTEM_PROMPT,
    LOCAL_TOOLS,
    PENDING_CONFIRMATION_TOOLS,
    PENDING_SYSTEM_PROMPT,
)


@dataclass(frozen=True)
class LocalDecision:
    tool_name: Optional[str]
    arguments: Dict[str, Any]
    message: str
    duration_ms: float


class LocalRouter:
    def __init__(self, client: OllamaClient, state: ConversationState) -> None:
        self.client = client
        self.state = state

    def route(self, user_text: str) -> LocalDecision:
        messages = [{"role": "system", "content": LOCAL_SYSTEM_PROMPT}]
        messages.extend(self.state.messages())
        messages.append({"role": "system", "content": self.state.research_context()})
        messages.append({"role": "user", "content": user_text})

        response = self.client.chat(messages, LOCAL_TOOLS)
        return _decision_from_response(response)


class PendingActionRouter:
    """Interpret one reply with pending context before ordinary routing."""

    def __init__(self, client: OllamaClient, state: ConversationState) -> None:
        self.client = client
        self.state = state

    def route(self, user_text: str) -> LocalDecision:
        pending = self.state.pending_action
        if pending is None:
            return LocalDecision(None, {}, "There is no pending action.", 0.0)
        messages = [{"role": "system", "content": PENDING_SYSTEM_PROMPT}]
        messages.extend(self.state.messages())
        messages.append(
            {
                "role": "system",
                "content": (
                    f"Pending tool: {pending.tool_name}\n"
                    f"Description: {pending.description}\n"
                    f"Warning: {pending.warning}\n"
                    "The stored arguments are owned by Python and must not be regenerated."
                ),
            }
        )
        messages.append({"role": "user", "content": user_text})
        response = self.client.chat(messages, PENDING_CONFIRMATION_TOOLS)
        decision = _decision_from_response(response)
        deterministic_intent = classify_pending_response(user_text)
        if deterministic_intent == UNCERTAIN_PENDING_RESPONSE:
            return LocalDecision(None, {}, TRASH_WARNING, response.duration_ms)
        if deterministic_intent is not None:
            arguments = (
                decision.arguments
                if decision.tool_name == deterministic_intent
                else {}
            )
            return LocalDecision(
                deterministic_intent, arguments, "", response.duration_ms
            )
        if decision.tool_name is not None:
            return decision
        return LocalDecision("route_new_request", {}, "", response.duration_ms)


def _decision_from_response(response) -> LocalDecision:
    if len(response.tool_calls) > 1:
        return LocalDecision(
            None,
            {},
            "SABEL rejected multiple local tool requests. Please ask for one action.",
            response.duration_ms,
        )
    if not response.tool_calls:
        return LocalDecision(
            None,
            {},
            response.content or "Please clarify which supported action you want.",
            response.duration_ms,
        )
    call = response.tool_calls[0]
    return LocalDecision(call.name, call.arguments, "", response.duration_ms)
