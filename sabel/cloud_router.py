"""Apply off/ask/auto policy before constructing the OpenAI research service."""

from dataclasses import dataclass
from typing import Callable, List, Optional

from sabel.config import Settings
from sabel.conversation_state import ConversationState
from sabel.errors import CloudResearchError
from sabel.openai_research import OpenAIResearchService
from sabel.tool_dispatcher import DelegationRequest


CLOUD_PROMPT = (
    "This request requires cloud research and may use OpenAI API credits.\n"
    "Use OpenAI for this request? [y/N] "
)


@dataclass(frozen=True)
class CloudResult:
    message: str
    used_cloud: bool = False
    sources: Optional[List[str]] = None
    duration_ms: float = 0.0
    usage: object = None


class CloudRouter:
    def __init__(
        self,
        settings: Settings,
        state: ConversationState,
        service_factory: Callable[[], OpenAIResearchService],
    ) -> None:
        self.settings = settings
        self.state = state
        self.service_factory = service_factory

    def handle(
        self,
        request: DelegationRequest,
        approval_callback: Optional[Callable[[str], str]] = None,
    ) -> CloudResult:
        if self.settings.cloud_mode == "off":
            return CloudResult(
                "Cloud processing is disabled. I can open a normal browser search instead."
            )
        if self.settings.cloud_mode == "ask":
            answer = approval_callback(CLOUD_PROMPT) if approval_callback else ""
            if answer.strip().casefold() not in {"y", "yes"}:
                return CloudResult("Cloud research cancelled. No API request was made.")
        if not self.settings.openai_api_key:
            return CloudResult(
                "Cloud research is unavailable because OPENAI_API_KEY is not set.\n"
                "Local SABEL commands still work normally."
            )

        try:
            research = self.service_factory().research(request.task)
        except CloudResearchError as error:
            return CloudResult(str(error))

        self.state.store_research(research.answer, research.sources)
        message = research.answer
        if research.sources:
            message += "\n\nSources:\n" + "\n".join(
                f"{index}. {url}" for index, url in enumerate(research.sources, 1)
            )
        return CloudResult(
            message,
            used_cloud=True,
            sources=research.sources,
            duration_ms=research.duration_ms,
            usage=research.usage,
        )

