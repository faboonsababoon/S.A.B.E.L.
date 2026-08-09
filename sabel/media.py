"""Deterministic media-slot extraction and truthful capability boundaries."""

from dataclasses import dataclass
import re
from typing import Optional


MEDIA_SERVICES = {"spotify", "youtube"}

_DIRECT_PLAY = re.compile(
    r"^\s*(?:please\s+)?(?:play|listen\s+to)(?:\s+(.*?))?\s*[.!?]*\s*$",
    re.I,
)
_EXPLICIT_SERVICE_MEDIA = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:open|find|search(?:\s+(?:for|up))?|play|listen\s+to)\s+"
    r"(?:(?:the\s+)?(?:song|track|album|artist|playlist|music)\s+)?"
    r"(?P<query>.+?)\s+(?:on|in)\s+(?P<service>spotify|youtube)"
    r"(?:\s+(?:on|in|using|with)\s+(?:my\s+)?(?:personal|nyu|school)"
    r"(?:\s+(?:chrome\s+)?(?:profile|browser|account))?)?\s*[.!?]*\s*$",
    re.I,
)


@dataclass(frozen=True)
class MediaRequest:
    query: Optional[str]
    service: Optional[str]


def parse_media_request(text: str) -> Optional[MediaRequest]:
    """Parse media wording while keeping the content and service separate."""
    explicit = _EXPLICIT_SERVICE_MEDIA.match(text)
    if explicit:
        query = " ".join(explicit.group("query").split()).strip(" .!?")
        return MediaRequest(query or None, explicit.group("service").casefold())

    match = _DIRECT_PLAY.match(text)
    if not match:
        return None
    remainder = " ".join((match.group(1) or "").split()).strip(" .!?")
    if not remainder:
        return MediaRequest(None, None)
    service = None
    service_match = re.search(r"\s+on\s+(spotify|youtube)\s*$", remainder, re.I)
    if service_match:
        service = service_match.group(1).casefold()
        remainder = remainder[: service_match.start()].strip(" .!?")
    return MediaRequest(remainder or None, service)


def normalize_music_service(value: Optional[str]) -> Optional[str]:
    if value is None or not value.strip():
        return None
    normalized = value.strip().casefold()
    if normalized not in MEDIA_SERVICES:
        raise ValueError("Default music service must be Spotify or YouTube.")
    return normalized
