"""Reusable Ollama client using native chat tool calls."""

from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional

from sabel.config import Settings
from sabel.errors import OllamaUnavailableError


@dataclass(frozen=True)
class LocalToolCall:
    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class OllamaResponse:
    content: str
    tool_calls: List[LocalToolCall]
    duration_ms: float


class OllamaClient:
    """Own one reusable SDK client and the configured local model."""

    def __init__(self, settings: Settings, client: Any = None) -> None:
        self.settings = settings
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from ollama import Client
        except ImportError as error:
            raise OllamaUnavailableError(
                "The Ollama Python package is missing. Run: "
                "python3 -m pip install -r requirements.txt"
            ) from error
        self._client = Client(host=self.settings.ollama_base_url)
        return self._client

    def is_available(self) -> bool:
        try:
            self._get_client().list()
            return True
        except Exception:
            return False

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> OllamaResponse:
        started = time.perf_counter()
        try:
            response = self._get_client().chat(
                model=self.settings.ollama_model,
                messages=messages,
                tools=tools,
                stream=False,
                think=False,
                keep_alive=self.settings.ollama_keep_alive,
                options={"temperature": 0},
            )
        except Exception as error:
            if getattr(error, "status_code", None) == 404:
                raise OllamaUnavailableError(
                    f'Local model "{self.settings.ollama_model}" is not installed. '
                    f"Run: ollama pull {self.settings.ollama_model}"
                ) from error
            raise OllamaUnavailableError(
                "Ollama is unavailable. Start Ollama and verify OLLAMA_BASE_URL."
            ) from error

        message = getattr(response, "message", None)
        if message is None and isinstance(response, dict):
            message = response.get("message", {})
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content", "")

        raw_calls = getattr(message, "tool_calls", None)
        if raw_calls is None and isinstance(message, dict):
            raw_calls = message.get("tool_calls", [])
        calls: List[LocalToolCall] = []
        for raw_call in raw_calls or []:
            function = getattr(raw_call, "function", None)
            if function is None and isinstance(raw_call, dict):
                function = raw_call.get("function", {})
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", None)
            if isinstance(function, dict):
                name = name or function.get("name")
                arguments = arguments if arguments is not None else function.get("arguments")
            calls.append(LocalToolCall(str(name or ""), arguments if isinstance(arguments, dict) else {}))

        return OllamaResponse(
            str(content or "").strip(),
            calls,
            (time.perf_counter() - started) * 1000,
        )

