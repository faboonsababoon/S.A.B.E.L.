"""Deterministic media-slot extraction and truthful capability boundaries."""

from dataclasses import dataclass
import re
from typing import Optional


MEDIA_SERVICES = {"spotify", "youtube"}


@dataclass(frozen=True)
class MediaRequest:
    query: Optional[str]
    service: Optional[str]


def parse_media_request(text: str) -> Optional[MediaRequest]:
    """Parse a direct play request and remove a trailing service phrase."""
    match = re.match(r"^\s*(?:please\s+)?play(?:\s+(.*?))?\s*[.!?]*\s*$", text, re.I)
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
