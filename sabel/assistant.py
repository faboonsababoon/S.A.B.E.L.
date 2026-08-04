"""Coordinate local routing, validation, actions, and optional cloud research."""

from dataclasses import dataclass
from typing import Callable, Optional

from sabel.cloud_router import CloudRouter
from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.errors import OllamaUnavailableError
from sabel.local_router import LocalRouter
from sabel.ollama_client import OllamaClient
from sabel.openai_research import OpenAIResearchService
from sabel.tool_dispatcher import ToolDispatcher


@dataclass(frozen=True)
class AssistantResult:
    message: str
    should_exit: bool = False


class SabelAssistant:
    def __init__(
        self,
        settings: Settings,
        ollama_client: Optional[OllamaClient] = None,
        state: Optional[ConversationState] = None,
        local_router: Optional[LocalRouter] = None,
        dispatcher: Optional[ToolDispatcher] = None,
        cloud_router: Optional[CloudRouter] = None,
    ) -> None:
        self.settings = settings
        self.state = state or ConversationState(settings.history_limit)
        self.ollama_client = ollama_client or OllamaClient(settings)
        self.local_router = local_router or LocalRouter(self.ollama_client, self.state)
        self.dispatcher = dispatcher or ToolDispatcher(
            settings, self.state, self.ollama_client.is_available
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
        if not user_text.strip():
            return AssistantResult("Please type a request.")
        try:
            decision = self.local_router.route(user_text)
        except OllamaUnavailableError as error:
            return AssistantResult(str(error))

        if not decision.tool_name:
            self.state.add_exchange(user_text, decision.message)
            return AssistantResult(self._debug(decision.message, decision.duration_ms, None, None))

        dispatched = self.dispatcher.dispatch(
            decision.tool_name, decision.arguments, user_text
        )
        if dispatched.delegation:
            cloud = self.cloud_router.handle(dispatched.delegation, approval_callback)
            self.state.add_exchange(user_text, cloud.message)
            return AssistantResult(
                self._debug(
                    cloud.message,
                    decision.duration_ms,
                    dispatched.selected_tool,
                    dispatched.validated_arguments,
                    cloud.duration_ms,
                    cloud.usage,
                )
            )

        self.state.add_exchange(user_text, dispatched.message)
        return AssistantResult(
            self._debug(
                dispatched.message,
                decision.duration_ms,
                dispatched.selected_tool,
                dispatched.validated_arguments,
            ),
            dispatched.should_exit,
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
            f"[debug] Selected tool: {tool or 'none'}",
            f"[debug] Validated arguments: {arguments or {}}",
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

