"""Convert typed input into structured SABEL commands."""

from dataclasses import dataclass
import re
from typing import Optional


@dataclass(frozen=True)
class Command:
    """A parsed command with an action and, when needed, a target."""

    action: str
    target: Optional[str] = None


_WEBSITE_PATTERN = re.compile(
    r"^open\s+(?:website|site)(?:\s+(.*))?$", re.IGNORECASE
)
_APPLICATION_PATTERN = re.compile(
    r"^open\s+(?:app|application)(?:\s+(.*))?$", re.IGNORECASE
)


def parse_command(text: str) -> Command:
    """Parse user text without performing any operating-system action."""
    cleaned = text.strip()

    if not cleaned:
        return Command("empty")

    lowered = cleaned.casefold()
    if lowered == "help":
        return Command("help")
    if lowered in {"quit", "exit"}:
        return Command("quit")

    website_match = _WEBSITE_PATTERN.fullmatch(cleaned)
    if website_match:
        target = (website_match.group(1) or "").strip()
        return Command("open_website", target)

    application_match = _APPLICATION_PATTERN.fullmatch(cleaned)
    if application_match:
        target = (application_match.group(1) or "").strip()
        return Command("open_application", target)

    return Command("unknown", cleaned)

