"""Ask Ollama to select one approved local tool or request clarification."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

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
        pending = self.state.pending_action is not None
        system_prompt = PENDING_SYSTEM_PROMPT if pending else LOCAL_SYSTEM_PROMPT
        tools = PENDING_CONFIRMATION_TOOLS if pending else LOCAL_TOOLS
        messages = [{"role": "system", "content": system_prompt}]
        if not pending:
            messages.extend(self.state.messages())
            messages.append({"role": "system", "content": self.state.research_context()})
        else:
            messages.append(
                {
                    "role": "system",
                    "content": f"Pending action: {self.state.pending_action.name}",
                }
            )
        messages.append({"role": "user", "content": user_text})

        response = self.client.chat(messages, tools)
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

