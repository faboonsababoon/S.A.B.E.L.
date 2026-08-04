"""Central configuration for SABEL's local and cloud providers."""

from dataclasses import dataclass
import os
from typing import Callable, Mapping, Optional

from sabel.keychain import read_openai_api_key


DEFAULT_OLLAMA_MODEL = "qwen3:1.7b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_KEEP_ALIVE = "1m"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_HISTORY_LIMIT = 10
DEFAULT_PENDING_ACTION_TTL = 60.0
DEFAULT_MAX_OUTPUT_TOKENS = 2000
DEFAULT_REQUEST_TIMEOUT = 60.0
CLOUD_MODES = {"off", "ask", "auto"}


@dataclass(frozen=True)
class Settings:
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_keep_alive: str = DEFAULT_OLLAMA_KEEP_ALIVE
    history_limit: int = DEFAULT_HISTORY_LIMIT
    pending_action_ttl: float = DEFAULT_PENDING_ACTION_TTL
    cloud_mode: str = "ask"
    openai_api_key: Optional[str] = None
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    openai_request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    debug: bool = False


def _positive_int(value: Optional[str], default: int) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: Optional[str], default: float) -> float:
    try:
        parsed = float(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def load_settings(
    environ: Optional[Mapping[str, str]] = None,
    cloud_mode: Optional[str] = None,
    debug: bool = False,
    credential_loader: Optional[Callable[[], Optional[str]]] = None,
) -> Settings:
    """Read configuration, preferring an environment key over macOS Keychain."""
    source = os.environ if environ is None else environ
    selected_mode = (cloud_mode or source.get("SABEL_CLOUD_MODE", "ask")).lower()
    if selected_mode not in CLOUD_MODES:
        raise ValueError("Cloud mode must be off, ask, or auto.")

    openai_api_key = source.get("OPENAI_API_KEY") or None
    if openai_api_key is None:
        loader = credential_loader
        if loader is None and environ is None:
            loader = read_openai_api_key
        if loader is not None:
            openai_api_key = loader()

    return Settings(
        ollama_model=source.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        ollama_base_url=source.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        ollama_keep_alive=source.get(
            "OLLAMA_KEEP_ALIVE", DEFAULT_OLLAMA_KEEP_ALIVE
        ),
        history_limit=_positive_int(
            source.get("SABEL_HISTORY_LIMIT"), DEFAULT_HISTORY_LIMIT
        ),
        pending_action_ttl=_positive_float(
            source.get("SABEL_PENDING_ACTION_TTL"), DEFAULT_PENDING_ACTION_TTL
        ),
        cloud_mode=selected_mode,
        openai_api_key=openai_api_key,
        openai_model=source.get(
            "OPENAI_MODEL",
            source.get("SABEL_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        ),
        openai_max_output_tokens=_positive_int(
            source.get("OPENAI_MAX_OUTPUT_TOKENS"), DEFAULT_MAX_OUTPUT_TOKENS
        ),
        openai_request_timeout=_positive_float(
            source.get("OPENAI_REQUEST_TIMEOUT"), DEFAULT_REQUEST_TIMEOUT
        ),
        debug=debug,
    )
