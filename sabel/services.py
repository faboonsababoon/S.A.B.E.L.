"""Authoritative names and URLs for supported public services."""

from dataclasses import dataclass
import re
from typing import Optional


@dataclass(frozen=True)
class Service:
    display_name: str
    url: Optional[str]
    default_profile: Optional[str] = None
    allowed_domains: frozenset[str] = frozenset()


SERVICE_REGISTRY = {
    "youtube": Service(
        "YouTube", "https://www.youtube.com/", "personal", frozenset({"youtube.com"})
    ),
    "google": Service(
        "Google", "https://www.google.com/", "personal", frozenset({"google.com"})
    ),
    "github": Service(
        "GitHub", "https://github.com/", "personal", frozenset({"github.com"})
    ),
    "albert": Service("Albert", None, "nyu", frozenset()),
    "nyu_gmail": Service(
        "NYU Gmail", "https://mail.google.com/", "nyu", frozenset({"google.com"})
    ),
    "personal_gmail": Service(
        "Personal Gmail",
        "https://mail.google.com/",
        "personal",
        frozenset({"google.com"}),
    ),
}

# The legacy macOS URL opener is intentionally limited to account-neutral
# public services. Account-scoped services must pass through the authenticated
# browser bridge so Python can enforce the Personal/NYU profile choice.
LEGACY_OPEN_SERVICE_NAMES = frozenset({"youtube", "google", "github"})

SERVICE_ALIASES = {
    "youtube": "youtube",
    "google": "google",
    "github": "github",
    "albert": "albert",
    "nyugmail": "nyu_gmail",
    "gmailnyu": "nyu_gmail",
    "personalgmail": "personal_gmail",
    "gmailpersonal": "personal_gmail",
}


@dataclass(frozen=True)
class ServiceResolution:
    service_name: Optional[str]
    service: Optional[Service]
    clarification: Optional[str] = None


def normalize_service_name(value: str) -> str:
    """Return a registry key without accepting paths, URLs, or commands."""
    cleaned = " ".join(value.casefold().strip().split())
    if not cleaned:
        raise ValueError("Please provide a service name.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9 ._-]{0,63}", cleaned):
        raise ValueError("That service name is not supported.")
    return cleaned.replace(" ", "")


def resolve_service(value: str) -> Optional[Service]:
    """Resolve a normalized service name through the reviewed registry."""
    try:
        key = normalize_service_name(value)
    except ValueError:
        return None
    return SERVICE_REGISTRY.get(SERVICE_ALIASES.get(key, key))


def resolve_profile_service(
    value: str,
    *,
    albert_url: Optional[str] = None,
    default_gmail_profile: Optional[str] = None,
) -> ServiceResolution:
    """Resolve a service and profile without inspecting cookies or account data."""
    try:
        normalized = normalize_service_name(value)
    except ValueError:
        return ServiceResolution(None, None)
    if normalized == "gmail":
        if default_gmail_profile in {"personal", "nyu"}:
            normalized = f"{default_gmail_profile}_gmail".replace(" ", "")
            key = "personal_gmail" if default_gmail_profile == "personal" else "nyu_gmail"
        else:
            return ServiceResolution(
                None,
                None,
                "Which Gmail account should I use: Personal or NYU?",
            )
    else:
        key = SERVICE_ALIASES.get(normalized, normalized)
    service = SERVICE_REGISTRY.get(key)
    if service is None:
        return ServiceResolution(None, None)
    if key == "albert":
        configured = Service(
            "Albert",
            albert_url.strip() if isinstance(albert_url, str) and albert_url.strip() else None,
            "nyu",
            frozenset(),
        )
        return ServiceResolution(key, configured)
    return ServiceResolution(key, service)


def service_mentioned(text: str) -> Optional[tuple[str, Service]]:
    """Find a registered service token in user text for safe router recovery."""
    folded = text.casefold()
    phrases = (
        ("personal gmail", "personal_gmail"),
        ("nyu gmail", "nyu_gmail"),
        ("youtube", "youtube"),
        ("google", "google"),
        ("github", "github"),
        ("albert", "albert"),
        ("gmail", "gmail"),
    )
    for phrase, key in phrases:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded):
            if key == "gmail":
                return None
            return key, SERVICE_REGISTRY[key]
    return None
