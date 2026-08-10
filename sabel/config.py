"""Central configuration for SABEL's local and cloud providers."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

from sabel.keychain import read_openai_api_key
from sabel.media import normalize_music_service


DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_KEEP_ALIVE = "1m"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_HISTORY_LIMIT = 10
DEFAULT_PENDING_ACTION_TTL = 60.0
DEFAULT_CLARIFICATION_TTL = 60.0
DEFAULT_BROWSER_REFERENCE_TTL = 300.0
DEFAULT_MAX_OUTPUT_TOKENS = 2000
DEFAULT_REQUEST_TIMEOUT = 60.0
DEFAULT_BROWSER_BRIDGE_PORT = 8765
DEFAULT_BROWSER_MAX_ACTIONS = 15
CLOUD_MODES = {"off", "ask", "auto"}


@dataclass(frozen=True)
class Settings:
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_keep_alive: str = DEFAULT_OLLAMA_KEEP_ALIVE
    history_limit: int = DEFAULT_HISTORY_LIMIT
    pending_action_ttl: float = DEFAULT_PENDING_ACTION_TTL
    clarification_ttl: float = DEFAULT_CLARIFICATION_TTL
    browser_reference_ttl: float = DEFAULT_BROWSER_REFERENCE_TTL
    default_music_service: Optional[str] = None
    default_search_engine: Optional[str] = None
    browser_bridge_port: int = DEFAULT_BROWSER_BRIDGE_PORT
    browser_max_actions: int = DEFAULT_BROWSER_MAX_ACTIONS
    browser_token_path: Path = Path.home() / ".sabel" / "browser-token"
    browser_origin_path: Path = Path.home() / ".sabel" / "browser-origins.json"
    browser_audit_path: Path = Path.home() / ".sabel" / "browser-actions.json"
    albert_url: Optional[str] = None
    default_gmail_profile: Optional[str] = None
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


def _browser_port(value: Optional[str]) -> int:
    port = _positive_int(value, DEFAULT_BROWSER_BRIDGE_PORT)
    if port > 65535:
        raise ValueError("Browser bridge port must be between 1 and 65535.")
    return port


def _optional_profile(value: Optional[str]) -> Optional[str]:
    if value is None or not value.strip():
        return None
    normalized = value.strip().casefold()
    if normalized not in {"personal", "nyu"}:
        raise ValueError("Default Gmail profile must be personal or nyu.")
    return normalized


def _optional_search_engine(value: Optional[str]) -> Optional[str]:
    if value is None or not value.strip():
        return None
    normalized = value.strip().casefold()
    if normalized not in {"google", "bing", "duckduckgo"}:
        raise ValueError(
            "Default search engine must be google, bing, or duckduckgo."
        )
    return normalized


def _optional_https_url(value: Optional[str]) -> Optional[str]:
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("SABEL_ALBERT_URL must be a credential-free HTTPS URL.")
    return candidate


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
    default_music_service = normalize_music_service(
        source.get("SABEL_DEFAULT_MUSIC_SERVICE")
    )
    default_search_engine = _optional_search_engine(
        source.get("SABEL_DEFAULT_SEARCH_ENGINE")
    )
    default_gmail_profile = _optional_profile(
        source.get("SABEL_DEFAULT_GMAIL_PROFILE")
    )
    albert_url = _optional_https_url(source.get("SABEL_ALBERT_URL"))
    config_directory = Path(source.get("SABEL_CONFIG_DIR", str(Path.home() / ".sabel"))).expanduser()

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
        clarification_ttl=_positive_float(
            source.get("SABEL_CLARIFICATION_TTL"), DEFAULT_CLARIFICATION_TTL
        ),
        browser_reference_ttl=_positive_float(
            source.get("SABEL_BROWSER_REFERENCE_TTL"),
            DEFAULT_BROWSER_REFERENCE_TTL,
        ),
        default_music_service=default_music_service,
        default_search_engine=default_search_engine,
        browser_bridge_port=_browser_port(source.get("SABEL_BROWSER_BRIDGE_PORT")),
        browser_max_actions=_positive_int(
            source.get("SABEL_BROWSER_MAX_ACTIONS"), DEFAULT_BROWSER_MAX_ACTIONS
        ),
        browser_token_path=config_directory / "browser-token",
        browser_origin_path=config_directory / "browser-origins.json",
        browser_audit_path=config_directory / "browser-actions.json",
        albert_url=albert_url,
        default_gmail_profile=default_gmail_profile,
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
