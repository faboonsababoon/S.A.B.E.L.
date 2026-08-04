"""Lazy OpenAI Responses API research with hosted web search only."""

from dataclasses import dataclass
import time
from typing import Any, List, Optional
from urllib.parse import urlsplit

from sabel.config import Settings
from sabel.errors import CloudResearchError


RESEARCH_INSTRUCTIONS = """You are SABEL's cloud research component, not a computer controller.
Research the user's task with current web sources. Return a direct answer, evaluation criteria,
top results, why each was selected, important limitations or tradeoffs, sources, and the research date when freshness matters.
Clearly distinguish evidence from inference. Be concise but sufficiently detailed. Never propose or execute Mac actions."""


@dataclass(frozen=True)
class ResearchResult:
    answer: str
    sources: List[str]
    duration_ms: float
    usage: Any = None


def _value(item: Any, name: str, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _safe_source(url: Any) -> Optional[str]:
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _extract_sources(response: Any) -> List[str]:
    found: List[str] = []
    for item in _value(response, "output", []) or []:
        if _value(item, "type") == "web_search_call":
            action = _value(item, "action", {})
            for source in _value(action, "sources", []) or []:
                url = _safe_source(_value(source, "url"))
                if url and url not in found:
                    found.append(url)
        if _value(item, "type") == "message":
            for content in _value(item, "content", []) or []:
                for annotation in _value(content, "annotations", []) or []:
                    url = _safe_source(_value(annotation, "url"))
                    if url and url not in found:
                        found.append(url)
    return found


def _cloud_error(error: Exception, model: str) -> CloudResearchError:
    name = type(error).__name__
    code = str(getattr(error, "code", "") or "")
    if name == "AuthenticationError":
        reason = "OpenAI authentication failed."
    elif name == "RateLimitError" and code == "insufficient_quota":
        reason = "OpenAI API quota or project spending limit was reached."
    elif name == "RateLimitError":
        reason = "OpenAI rate limit was reached."
    elif name in {"APITimeoutError", "TimeoutException"}:
        reason = "The OpenAI request timed out."
    elif name in {"APIConnectionError", "ConnectError", "NetworkError"}:
        reason = "The OpenAI service could not be reached."
    elif name in {"NotFoundError", "PermissionDeniedError", "BadRequestError"}:
        reason = f'The cloud model "{model}" or web-search request is unavailable.'
    else:
        reason = "OpenAI returned an unexpected or malformed response."
    return CloudResearchError(
        "Cloud research is currently unavailable.\n"
        f"Reason: {reason}\n"
        "Local SABEL commands still work normally."
    )


class OpenAIResearchService:
    def __init__(self, settings: Settings, client: Any = None) -> None:
        self.settings = settings
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as error:
            raise CloudResearchError(
                "Cloud research is unavailable because the OpenAI package is missing."
            ) from error
        self._client = OpenAI(
            max_retries=0,
            timeout=self.settings.openai_request_timeout,
        )
        return self._client

    def research(self, task: str) -> ResearchResult:
        if not self.settings.openai_api_key:
            raise CloudResearchError(
                "Cloud research is unavailable because OPENAI_API_KEY is not set.\n"
                "Local SABEL commands still work normally."
            )
        started = time.perf_counter()
        try:
            response = self._get_client().responses.create(
                model=self.settings.openai_model,
                instructions=RESEARCH_INSTRUCTIONS,
                input=task,
                tools=[{"type": "web_search"}],
                tool_choice="auto",
                include=["web_search_call.action.sources"],
                reasoning={"effort": "low"},
                max_output_tokens=self.settings.openai_max_output_tokens,
                store=False,
            )
        except CloudResearchError:
            raise
        except Exception as error:
            raise _cloud_error(error, self.settings.openai_model) from error

        answer = getattr(response, "output_text", "")
        if not isinstance(answer, str) or not answer.strip():
            raise CloudResearchError(
                "Cloud research is currently unavailable.\n"
                "Reason: OpenAI returned no readable research answer.\n"
                "Local SABEL commands still work normally."
            )
        return ResearchResult(
            answer.strip(),
            _extract_sources(response),
            (time.perf_counter() - started) * 1000,
            getattr(response, "usage", None),
        )

