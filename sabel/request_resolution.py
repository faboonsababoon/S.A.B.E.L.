"""Authoritative current-turn constraint extraction and request resolution.

Ollama interprets language, but this module owns routing fields whose accidental
mutation could target the wrong application, provider, or Chrome profile.
"""

from dataclasses import dataclass
import re
from typing import Optional
import uuid

from sabel.application_catalog import ApplicationCatalog
from sabel.browser_routing import (
    normalize_search_engine,
    parse_browser_search_request,
    validate_search_query,
)
from sabel.conversation_state import ConversationState
from sabel.request_models import Intent, LockedConstraints, ResolvedRequest
from sabel.services import resolve_profile_service, service_mentioned


_PROFILE = re.compile(
    r"\b(?:in|on|using|with|from)?\s*(?:my\s+)?"
    r"(?P<profile>personal|nyu|school)"
    r"(?:\s+(?:chrome\s+)?(?:profile|browser|account))?\b",
    re.I,
)
_PROVIDER = re.compile(
    r"(?<![a-z0-9])(google|youtube|bing|duckduckgo)(?:\.com)?(?![a-z0-9])",
    re.I,
)
_CORRECTION = re.compile(
    r"^\s*(?:no\b|wrong\s+(?:application|app|profile|service|browser)\b|"
    r"not\s+that\b|i\s+meant\b)|\b(?:instead|not\s+[^,.]+\s*$)",
    re.I,
)
_APPLICATION_WORDING = re.compile(
    r"\b(?:application|desktop\s+app|installed\s+(?:application|app)|app|on\s+my\s+laptop)\b",
    re.I,
)
_CONTEXT_REFERENCE = re.compile(
    r"\b(?:do\s+(?:it\s+)?the\s+same|same\s+(?:thing|search|action)|"
    r"there|in\s+it|on\s+it|that\s+same)\b",
    re.I,
)
_FAILURE_REPORT = re.compile(
    r"^\s*(?:(?:its|it|that|this)(?:'s|\s+is)?\s+)?(?:not\s+working|did(?:n't|\s+not)\s+work|"
    r"failed|nothing\s+(?:happened|opened)|i\s+can(?:not|'t)\s+see\s+it)\s*[.!?]*$",
    re.I,
)
_NAMED_YOUTUBE_CONTENT = re.compile(
    r"^\s*(?:now\s+)?(?:open|find|play|look\s+up|search(?:\s+for)?)\s+"
    r"(?P<query>.+?)\s+(?:on|in|using)\s+youtube(?:\.com)?\b(?P<tail>.*)$",
    re.I,
)
_MEDIA = re.compile(r"^\s*(?:please\s+)?(?:play|listen\s+to)\s+(?P<query>.+?)\s*[.!?]*$", re.I)


@dataclass(frozen=True)
class ResolutionDecision:
    request: Optional[ResolvedRequest] = None
    tool_name: Optional[str] = None
    arguments: Optional[dict[str, object]] = None
    message: Optional[str] = None
    clarification_question: Optional[str] = None
    clarification_intent: Optional[str] = None
    collected_slots: Optional[dict[str, object]] = None
    missing_slots: tuple[str, ...] = ()


def extract_locked_constraints(text: str) -> LockedConstraints:
    """Extract a deliberately small set of authoritative current-turn fields."""
    profiles = {
        "nyu" if match.group("profile").casefold() == "school" else match.group("profile").casefold()
        for match in _PROFILE.finditer(text)
    }
    profile = next(iter(profiles)) if len(profiles) == 1 else None
    providers = {
        normalize_search_engine(match.group(1))
        for match in _PROVIDER.finditer(text)
        if not (
            match.group(1).casefold() == "google"
            and re.match(r"\s+chrome\b", text[match.end() :], re.I)
        )
    }
    providers.discard(None)
    provider = next(iter(providers)) if len(providers) == 1 else None
    quoted = _matching_quote(text)
    application_target = _application_target(text, quoted)
    context_reference = "same" if _CONTEXT_REFERENCE.search(text) else None
    return LockedConstraints(
        profile_id=profile,
        search_engine=provider,
        service=provider,
        application_name=application_target,
        quoted_text=quoted,
        explicit_application_intent=bool(_APPLICATION_WORDING.search(text)),
        correction=bool(_CORRECTION.search(text)) and not bool(_FAILURE_REPORT.fullmatch(text.strip())),
        context_reference=context_reference,
    )


class RequestResolver:
    """Merge a model interpretation with Python-owned constraints and state."""

    def __init__(
        self,
        state: ConversationState,
        application_catalog: ApplicationCatalog,
        *,
        default_music_service: Optional[str] = None,
        default_search_engine: Optional[str] = None,
    ) -> None:
        self.state = state
        self.application_catalog = application_catalog
        self.default_music_service = default_music_service
        self.default_search_engine = default_search_engine

    def resolve(
        self,
        text: str,
        *,
        locked: LockedConstraints,
        model_tool_name: Optional[str] = None,
        model_arguments: Optional[dict[str, object]] = None,
        model_intent: Optional[str] = None,
    ) -> Optional[ResolutionDecision]:
        turn_id = uuid.uuid4().hex

        if _FAILURE_REPORT.fullmatch(text.strip()):
            return ResolutionDecision(message=self.state.contextual_failure_message())

        if locked.context_reference == "same" and not re.search(
            r"\b(?:search|look\s+up|find|google)\b", text, re.I
        ):
            template = self.state.reusable_action
            if template is None:
                return ResolutionDecision(
                    message="I do not have a verified successful action to repeat yet."
                )
            request = template.resolve(
                turn_id=turn_id,
                raw_input=text,
                constraints=locked,
            )
            return _decision_for(request)

        media = self._media_request(text, locked, turn_id)
        if media is not None:
            return media

        search = parse_browser_search_request(
            text,
            reference=self.state.current_browser_reference(),
            last_query=self.state.last_browser_query,
            default_search_engine=self.default_search_engine,
        )
        if search is not None:
            query = locked.quoted_text or search.query
            engine = locked.search_engine or search.search_engine
            profile = locked.profile_id or search.profile_id
            if engine and profile and query:
                try:
                    query = validate_search_query(query)
                except ValueError:
                    query = ""
                query = _remove_routing_residue(query)
                if query:
                    request = ResolvedRequest(
                        intent=(Intent.SEARCH_YOUTUBE if engine == "youtube" else Intent.SEARCH_WEB),
                        service=engine,
                        search_engine=engine,
                        query=query,
                        profile_id=profile,
                        source_turn_id=turn_id,
                        raw_input=text,
                        model_intent=model_intent,
                        locked=locked,
                        resolution_trace=("deterministic browser-search slots", "current-turn locks applied"),
                    )
                    return _decision_for(request)

        named_youtube = _NAMED_YOUTUBE_CONTENT.match(text)
        if named_youtube:
            query = _remove_routing_residue(named_youtube.group("query"))
            if query:
                request = ResolvedRequest(
                    intent=Intent.SEARCH_YOUTUBE,
                    service="youtube",
                    search_engine="youtube",
                    query=query,
                    profile_id=locked.profile_id or "personal",
                    source_turn_id=turn_id,
                    raw_input=text,
                    model_intent=model_intent,
                    locked=locked,
                    resolution_trace=("named content on YouTube", "explicit profile wins"),
                )
                return _decision_for(request)

        homepage = _service_homepage(text, locked)
        if homepage is not None:
            service, profile = homepage
            request = ResolvedRequest(
                intent=Intent.OPEN_SERVICE,
                service=service,
                profile_id=profile,
                source_turn_id=turn_id,
                raw_input=text,
                model_intent=model_intent,
                locked=locked,
                resolution_trace=("registered service homepage", "explicit profile wins over service default"),
            )
            return _decision_for(request)

        application = self._application_request(text, locked, turn_id, model_intent)
        if application is not None:
            return application

        return self._merge_model(
            text,
            turn_id,
            locked,
            model_tool_name,
            model_arguments or {},
            model_intent,
        )

    def _application_request(
        self,
        text: str,
        locked: LockedConstraints,
        turn_id: str,
        model_intent: Optional[str],
    ) -> Optional[ResolutionDecision]:
        target = locked.application_name
        if not target:
            return None
        browser_markers = bool(
            locked.profile_id
            or locked.search_engine
            or re.search(r"\b(?:website|webpage|browser|tab|search|look\s+up)\b", text, re.I)
        )
        match = self.application_catalog.resolve(target)
        should_route = locked.explicit_application_intent or (
            not browser_markers and (match.application is not None or _looks_like_open(text))
        )
        if not should_route:
            return None
        resolved_name = match.application.display_name if match.application else target
        request = ResolvedRequest(
            intent=Intent.OPEN_APPLICATION,
            application_name=resolved_name,
            source_turn_id=turn_id,
            raw_input=text,
            model_intent=model_intent,
            locked=locked,
            resolution_trace=(
                "application intent separated from browser context",
                "installed-application catalog will make final selection",
            ),
        )
        return _decision_for(request)

    def _media_request(
        self, text: str, locked: LockedConstraints, turn_id: str
    ) -> Optional[ResolutionDecision]:
        match = _MEDIA.match(text)
        if not match:
            return None
        query = match.group("query").strip()
        service = locked.service if locked.service in {"youtube", "spotify"} else None
        if service is None and re.search(
            r"(?<![a-z0-9])spotify(?![a-z0-9])", text, re.I
        ):
            service = "spotify"
        if service:
            query = re.sub(r"\s+(?:on|in)\s+(?:youtube|spotify)\s*$", "", query, flags=re.I).strip()
        service = service or self.default_music_service
        if service == "youtube":
            request = ResolvedRequest(
                Intent.SEARCH_YOUTUBE,
                service="youtube",
                search_engine="youtube",
                query=query,
                profile_id=locked.profile_id or "personal",
                source_turn_id=turn_id,
                raw_input=text,
                locked=locked,
                resolution_trace=("media request", "YouTube search"),
            )
            return _decision_for(request)
        if service == "spotify":
            request = ResolvedRequest(
                Intent.OPEN_SPOTIFY_SEARCH,
                service="spotify",
                query=query,
                source_turn_id=turn_id,
                raw_input=text,
                locked=locked,
                resolution_trace=("media request", "Spotify search"),
            )
            return _decision_for(request)
        display = query[:1].upper() + query[1:]
        return ResolutionDecision(
            clarification_question=f"Which service should I use for “{display}”: Spotify or YouTube?",
            clarification_intent="media_search",
            collected_slots={"query": query, **({"profile_id": locked.profile_id} if locked.profile_id else {})},
            missing_slots=("service",),
        )

    def _merge_model(
        self,
        text: str,
        turn_id: str,
        locked: LockedConstraints,
        name: Optional[str],
        arguments: dict[str, object],
        model_intent: Optional[str],
    ) -> Optional[ResolutionDecision]:
        if name == "open_application":
            supplied = locked.application_name or arguments.get("application_name")
            if isinstance(supplied, str) and supplied.strip():
                request = ResolvedRequest(
                    Intent.OPEN_APPLICATION,
                    application_name=supplied.strip(),
                    source_turn_id=turn_id,
                    raw_input=text,
                    model_intent=model_intent or name,
                    locked=locked,
                    resolution_trace=("model intent", "locked application merged"),
                )
                return _decision_for(request)
        if name in {"search_web", "search_youtube"}:
            engine = locked.search_engine or (
                "youtube" if name == "search_youtube" else normalize_search_engine(arguments.get("search_engine"))
            )
            profile = locked.profile_id or arguments.get("profile_id")
            query = locked.quoted_text or arguments.get("query")
            if isinstance(query, str):
                query = _remove_routing_residue(query)
            if engine and profile in {"personal", "nyu"} and query:
                request = ResolvedRequest(
                    Intent.SEARCH_YOUTUBE if engine == "youtube" else Intent.SEARCH_WEB,
                    service=engine,
                    search_engine=engine,
                    query=str(query),
                    profile_id=str(profile),
                    source_turn_id=turn_id,
                    raw_input=text,
                    model_intent=model_intent or name,
                    locked=locked,
                    resolution_trace=("model intent", "locked profile/provider/query merged"),
                )
                return _decision_for(request)
        if name == "open_service":
            service = locked.service or arguments.get("service_name")
            if isinstance(service, str):
                resolution = resolve_profile_service(service)
                default_profile = resolution.service.default_profile if resolution.service else None
                profile = locked.profile_id or arguments.get("profile_id") or default_profile
                if profile in {"personal", "nyu"}:
                    request = ResolvedRequest(
                        Intent.OPEN_SERVICE,
                        service=service.casefold(),
                        profile_id=str(profile),
                        source_turn_id=turn_id,
                        raw_input=text,
                        model_intent=model_intent or name,
                        locked=locked,
                        resolution_trace=("model intent", "explicit profile merged before service default"),
                    )
                    return _decision_for(request)
        return None


def _decision_for(request: ResolvedRequest) -> ResolutionDecision:
    if request.intent == Intent.OPEN_APPLICATION:
        return ResolutionDecision(request, "open_application", {"application_name": request.application_name or ""})
    if request.intent == Intent.OPEN_SERVICE:
        return ResolutionDecision(request, "open_service", {"service_name": request.service or "", "profile_id": request.profile_id or ""})
    if request.intent == Intent.SEARCH_YOUTUBE:
        return ResolutionDecision(request, "search_youtube", {"query": request.query or "", "profile_id": request.profile_id or ""})
    if request.intent == Intent.SEARCH_WEB:
        return ResolutionDecision(
            request,
            "search_web",
            {"query": request.query or "", "search_engine": request.search_engine or "", "profile_id": request.profile_id or ""},
        )
    if request.intent == Intent.OPEN_SPOTIFY_SEARCH:
        return ResolutionDecision(request, "open_spotify_search", {"query": request.query or ""})
    return ResolutionDecision(request=request)


def _application_target(text: str, quoted: Optional[str]) -> Optional[str]:
    if quoted and (_looks_like_open(text) or _APPLICATION_WORDING.search(text)):
        return quoted
    match = re.search(r"\b(?:open|launch|start|run)\s+(?P<target>.+)", text, re.I)
    if not match:
        return None
    target = match.group("target")
    target = re.split(r"[.!?]", target, maxsplit=1)[0]
    target = re.split(r"\s*,?\s+not\s+", target, maxsplit=1, flags=re.I)[0]
    target = re.sub(
        r"\s+(?:on|in|using|with)\s+(?:my\s+)?(?:personal|nyu|school)"
        r"(?:\s+(?:chrome\s+)?(?:profile|browser|account))?.*$",
        "",
        target,
        flags=re.I,
    )
    target = re.sub(r"\s+(?:on\s+my\s+laptop|desktop\s+app|application|app)\s*$", "", target, flags=re.I)
    target = re.sub(r"^the\s+", "", target, flags=re.I)
    target = target.strip(' \t"“”\'')
    if target.casefold() in {"it", "that", "this", "the app", "the application"}:
        return None
    return target or None


def _service_homepage(text: str, locked: LockedConstraints) -> Optional[tuple[str, str]]:
    if not _looks_like_open(text) or re.search(r"\b(?:search|find|look\s+up)\b", text, re.I):
        return None
    mentioned = service_mentioned(text)
    service_name = locked.service or (mentioned[0] if mentioned else None)
    if not service_name:
        return None
    words = re.findall(r"[a-z0-9]+", text.casefold())
    framing = {
        "a", "account", "browser", "can", "chrome", "could", "go", "in", "me", "my", "navigate",
        "nyu", "on", "open", "page", "personal", "please", "profile", "pull", "school", "take", "the",
        "to", "up", "using", "visit", "website", "with", "would", "you", service_name,
    }
    if [word for word in words if word not in framing]:
        return None
    resolution = resolve_profile_service(service_name)
    default = resolution.service.default_profile if resolution.service else None
    return service_name, locked.profile_id or default or "personal"


def _matching_quote(text: str) -> Optional[str]:
    for opening, closing in (("\"", "\""), ("“", "”"), ("'", "'"), ("‘", "’")):
        start = text.find(opening)
        if start >= 0:
            end = text.find(closing, start + 1)
            if end > start + 1:
                return text[start + 1 : end].strip()
    return None


def _remove_routing_residue(query: str) -> str:
    value = _PROFILE.sub(" ", query)
    value = re.sub(r"\b(?:on|in|using|with)\s+(?:google|youtube|bing|duckduckgo)(?:\.com)?\b", " ", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" \t,;:.!?\"“”")
    return value


def _looks_like_open(text: str) -> bool:
    return bool(re.search(r"\b(?:open|launch|start|run|go\s+to|take\s+me\s+to|pull\s+up|visit|navigate)\b", text, re.I))
