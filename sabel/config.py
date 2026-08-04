"""Read SABEL Phase 2 settings from environment variables."""

from dataclasses import dataclass
import os
from typing import Mapping, Optional


DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class Settings:
    """Configuration values used by SABEL's OpenAI command router."""

    api_key: Optional[str]
    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def load_settings(environ: Optional[Mapping[str, str]] = None) -> Settings:
    """Load configuration without printing or otherwise exposing secrets."""
    source = os.environ if environ is None else environ
    api_key = source.get("OPENAI_API_KEY") or None
    model = source.get("SABEL_OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return Settings(api_key=api_key, model=model)

