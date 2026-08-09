"""Deterministic browser-search slot cleanup, validation, and URL verification."""

from dataclasses import dataclass
import re
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlsplit

from sabel.browser_models import LastBrowserReference


PROFILE_IDS = frozenset({"personal", "nyu"})
SEARCH_PROVIDER_URLS = {
    "google": ("Google", "https://www.google.com/search", "q", frozenset({"google.com"})),
    "bing": ("Bing", "https://www.bing.com/search", "q", frozenset({"bing.com"})),
    "duckduckgo": (
        "DuckDuckGo",
        "https://duckduckgo.com/",
        "q",
        frozenset({"duckduckgo.com"}),
    ),
    "youtube": (
        "YouTube",
        "https://www.youtube.com/results",
        "search_query",
        frozenset({"youtube.com"}),
    ),
}
WEB_SEARCH_ENGINES = frozenset({"google", "bing", "duckduckgo"})
PROVIDER_ALIASES = {
    "google": "google",
    "google.com": "google",
    "bing": "bing",
    "bing.com": "bing",
    "duckduckgo": "duckduckgo",
    "duckduckgo.com": "duckduckgo",
    "youtube": "youtube",
    "youtube.com": "youtube",
}

_SEARCH_VERB = re.compile(r"\b(?:search(?:\s+up)?|look\s+up|find)\b", re.I)
_PROVIDER_SHORTHAND = re.compile(
    r"^\s*(?P<provider>ggoogle|google|youtube|bing|duckduckgo)(?:\.com)?"
    r"\s+(?P<query>.+?)\s*$",
    re.I,
)
_PROFILE = re.compile(
    r"\b(?:in|on|using|with)\s+(?:my\s+)?(?P<profile>personal|nyu)"
    r"(?:\s+(?:chrome\s+)?(?:profile|browser))?\b"
    r"|\b(?P<profile_first>personal|nyu)\s+(?:chrome\s+)?profile\b",
    re.I,
)
_SAME_PROFILE = re.compile(r"\b(?:the\s+)?same\s+profile\b", re.I)
_SAME_QUERY = re.compile(
    r"\b(?:that\s+|the\s+)?(?:same\s+)?(?:string|search|query|thing|one|it)\b",
    re.I,
)
_CONTEXT_REFERENCE = re.compile(
    r"\b(?:there|in\s+it|on\s+it|same\s+(?:tab|place|profile|thing|search|query))\b",
    re.I,
)
_PROVIDER_AFTER_PREPOSITION = re.compile(
    r"\b(?:on|in|using)\s+(google|youtube|bing|duckduckgo)(?:\.com)?\b",
    re.I,
)
_PROVIDER_BEFORE_SEARCH = re.compile(
    r"\b(?:go|navigate)\s+to\s+(google|youtube|bing|duckduckgo)(?:\.com)?"
    r"\s+and\s*$",
    re.I,
)
_PROVIDER_DIRECT_SEARCH = re.compile(
    r"^\s*(google|youtube|bing|duckduckgo)(?:\.com)?\s+for\b", re.I
)
_PROVIDER_PREFIX_CONTEXT = re.compile(
    r"\b(?:in|on)\s+(google|youtube|bing|duckduckgo)(?:\.com)?\s*$", re.I
)
_ROUTING_RESIDUE = re.compile(
    r"^\s*(?:(?:now\s+)?go\s+to|search\s+up)\b"
    r"|\b(?:in|on|using|with)\s+(?:my\s+)?(?:personal|nyu)"
    r"(?:\s+(?:chrome\s+)?(?:profile|browser))?\b"
    r"|\b(?:in|on|using)\s+(?:google|youtube|bing|duckduckgo)(?:\.com)?\b"
    r"|\b(?:the\s+)?same\s+(?:profile|tab|place)\b",
    re.I,
)


@dataclass(frozen=True)
class ParsedBrowserSearch:
    query: str
    search_engine: Optional[str]
    profile_id: Optional[str]
    explicit_search_engine: Optional[str] = None
    explicit_profile_id: Optional[str] = None
    references_context: bool = False
    missing_context: Optional[str] = None
    routing_conflict: Optional[str] = None


def normalize_search_engine(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    if normalized == "default":
        return "google"
    return PROVIDER_ALIASES.get(normalized)


def search_provider_display_name(search_engine: str) -> str:
    return SEARCH_PROVIDER_URLS[search_engine][0]


def _matching_quote(text: str) -> Optional[str]:
    pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
    positions = [(text.find(opening), opening) for opening in pairs if text.find(opening) >= 0]
    if not positions:
        return None
    start, opening = min(positions)
    closing = pairs[opening]
    end = text.find(closing, start + 1)
    if end < 0:
        return None
    return text[start + 1 : end].strip()


def _explicit_provider(text: str, search_start: int, search_end: int) -> Optional[str]:
    before = text[:search_start]
    after = text[search_end:]
    found: list[str] = []
    for pattern, value in (
        (_PROVIDER_BEFORE_SEARCH, before),
        (_PROVIDER_PREFIX_CONTEXT, before),
        (_PROVIDER_DIRECT_SEARCH, after),
    ):
        match = pattern.search(value)
        if match:
            found.append(match.group(1).casefold())
    found.extend(match.group(1).casefold() for match in _PROVIDER_AFTER_PREPOSITION.finditer(after))
    normalized = {normalize_search_engine(value) for value in found}
    normalized.discard(None)
    if len(normalized) > 1:
        return "__conflict__"
    return next(iter(normalized)) if normalized else None


def _clean_unquoted_query(tail: str) -> str:
    value = _PROFILE.sub(" ", tail)
    value = re.sub(
        r"\b(?:in|on|using)\s+(?:google|youtube|bing|duckduckgo)(?:\.com)?\b",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b(?:in|on)\s+(?:it|there)\s*$", " ", value, flags=re.I)
    value = re.sub(r"\bthere\s*$", " ", value, flags=re.I)
    value = re.sub(r"\b(?:the\s+)?same\s+(?:profile|tab|place)\b", " ", value, flags=re.I)
    value = re.sub(
        r"^\s*(?:google|youtube|bing|duckduckgo)(?:\.com)?\s+for\b",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(r"^\s*(?:up|for)\b", " ", value, flags=re.I)
    value = re.sub(r"^\s*(?:and|then)\b", " ", value, flags=re.I)
    # A discourse connector can remain after routing phrases are removed from
    # inputs such as "search the same thing on YouTube but on Personal".
    if _SAME_QUERY.search(value):
        value = re.sub(r"\b(?:but|and)\s*$", " ", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" \t,;:")
    if value[:1] in {'"', "'", "“", "‘"} and value[-1:] not in {'"', "'", "”", "’"}:
        value = value[1:].strip()
    if value[-1:] in {'"', "'", "”", "’"} and value[:1] not in {'"', "'", "“", "‘"}:
        value = value[:-1].strip()
    if len(value) >= 2 and (
        (value[0] == value[-1] and value[0] in {'"', "'"})
        or (value[0], value[-1]) in {("“", "”"), ("‘", "’")}
    ):
        value = value[1:-1].strip()
    return value


def parse_browser_search_request(
    text: str,
    *,
    reference: Optional[LastBrowserReference] = None,
    last_query: Optional[str] = None,
    default_search_engine: Optional[str] = None,
) -> Optional[ParsedBrowserSearch]:
    """Extract only explicit routing metadata and the actual literal query."""

    match = _SEARCH_VERB.search(text)
    shorthand = _PROVIDER_SHORTHAND.match(text) if match is None else None
    if match is None and shorthand is None:
        return None
    if (
        shorthand is not None
        and shorthand.group("provider").casefold() in {"google", "ggoogle"}
        and re.match(r"chrome(?:\s|$)", shorthand.group("query"), re.I)
    ):
        return None
    profile_matches = list(_PROFILE.finditer(text))
    explicit_profiles = {
        (item.group("profile") or item.group("profile_first") or "").casefold()
        for item in profile_matches
    }
    explicit_profiles.discard("")
    profile_conflict = len(explicit_profiles) > 1
    explicit_profile = (
        next(iter(explicit_profiles)) if len(explicit_profiles) == 1 else None
    )
    references_context = bool(
        _CONTEXT_REFERENCE.search(text)
        or _SAME_PROFILE.search(text)
        or _SAME_QUERY.search(text)
    )
    if shorthand is not None:
        shorthand_provider = shorthand.group("provider").casefold()
        provider = "google" if shorthand_provider == "ggoogle" else shorthand_provider
        suffix_providers = {
            normalize_search_engine(item.group(1))
            for item in _PROVIDER_AFTER_PREPOSITION.finditer(
                shorthand.group("query")
            )
        }
        suffix_providers.discard(None)
        if any(item != provider for item in suffix_providers):
            provider = "__conflict__"
    else:
        assert match is not None
        provider = _explicit_provider(text, match.start(), match.end())
    provider_conflict = provider == "__conflict__"
    if provider_conflict:
        provider = None

    tail = shorthand.group("query") if shorthand is not None else text[match.end() :]
    quoted = _matching_quote(tail)
    query = quoted if quoted is not None else _clean_unquoted_query(tail)
    if re.fullmatch(
        r"(?:that\s+|the\s+)?(?:same\s+)?(?:string|search|query|thing|one|it)"
        r"(?:\s+up)?(?:\s+(?:again|please))?",
        query,
        re.I,
    ):
        query = last_query or ""

    routing_conflict = (
        "profile" if profile_conflict else "search_engine" if provider_conflict else None
    )
    missing_context = routing_conflict
    resolved_profile = explicit_profile
    resolved_provider = provider
    if references_context and routing_conflict is None:
        if reference is None:
            if _SAME_PROFILE.search(text):
                missing_context = "profile"
            if resolved_provider is None:
                missing_context = missing_context or "search_engine"
        else:
            resolved_profile = resolved_profile or reference.profile_id
            resolved_provider = resolved_provider or reference.search_engine or reference.service
    elif routing_conflict is None and resolved_provider is None and reference is not None:
        resolved_provider = reference.search_engine or reference.service
        resolved_profile = resolved_profile or reference.profile_id

    resolved_provider = normalize_search_engine(
        resolved_provider or (default_search_engine if routing_conflict is None else "") or ""
    )
    if (
        routing_conflict != "profile"
        and resolved_profile is None
        and resolved_provider is not None
    ):
        resolved_profile = "personal"

    return ParsedBrowserSearch(
        query=query,
        search_engine=resolved_provider,
        profile_id=resolved_profile,
        explicit_search_engine=provider,
        explicit_profile_id=explicit_profile,
        references_context=references_context,
        missing_context=missing_context,
        routing_conflict=routing_conflict,
    )


def validate_search_query(value: object, *, maximum_length: int = 500) -> str:
    if not isinstance(value, str):
        raise ValueError("The browser search query must be text.")
    query = value.strip()
    if not query:
        raise ValueError("Please provide a search query.")
    if len(query) > maximum_length:
        raise ValueError("That browser search query is too long.")
    if any(ord(character) < 32 for character in query):
        raise ValueError("That browser search query is malformed.")
    if _ROUTING_RESIDUE.search(query):
        raise ValueError(
            "The browser search query still contains profile or provider routing instructions."
        )
    return query


def build_search_url(search_engine: str, query: str) -> str:
    selected = normalize_search_engine(search_engine)
    if selected not in SEARCH_PROVIDER_URLS:
        raise ValueError("That browser search engine is not supported.")
    cleaned = validate_search_query(query)
    _, base_url, parameter, _ = SEARCH_PROVIDER_URLS[selected]
    return base_url + "?" + urlencode({parameter: cleaned})


def verify_search_url(url: object, search_engine: str, query: str) -> bool:
    selected = normalize_search_engine(search_engine)
    if selected not in SEARCH_PROVIDER_URLS or not isinstance(url, str):
        return False
    _, _, parameter, domains = SEARCH_PROVIDER_URLS[selected]
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
    except ValueError:
        return False
    if parsed.scheme.casefold() not in {"http", "https"}:
        return False
    if not any(hostname == domain or hostname.endswith("." + domain) for domain in domains):
        return False
    values = parse_qs(parsed.query, keep_blank_values=True).get(parameter, [])
    return len(values) == 1 and values[0] == query
