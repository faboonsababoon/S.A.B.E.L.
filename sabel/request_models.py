"""Immutable request, action-history, and safe execution-evidence models."""

from dataclasses import dataclass, replace
from enum import Enum
import time
from typing import Optional


class Intent(Enum):
    OPEN_APPLICATION = "open_application"
    CHECK_APPLICATION = "check_application_installed"
    LIST_APPLICATIONS = "show_installed_applications"
    OPEN_SERVICE = "open_service"
    SEARCH_WEB = "search_web"
    SEARCH_YOUTUBE = "search_youtube"
    OPEN_SPOTIFY_SEARCH = "open_spotify_search"
    OTHER = "other"


class ActionResultStatus(Enum):
    ATTEMPTED = "attempted"
    VERIFIED = "verified"
    FAILED = "failed"
    REJECTED = "rejected_by_user"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class LockedConstraints:
    profile_id: Optional[str] = None
    search_engine: Optional[str] = None
    service: Optional[str] = None
    application_name: Optional[str] = None
    quoted_text: Optional[str] = None
    explicit_application_intent: bool = False
    correction: bool = False
    context_reference: Optional[str] = None

    def prompt_text(self) -> str:
        return (
            "Python-authoritative current-turn constraints\n"
            f"profile_id: {self.profile_id or 'not explicit'}\n"
            f"search_engine: {self.search_engine or 'not explicit'}\n"
            f"service: {self.service or 'not explicit'}\n"
            f"application_name: {self.application_name or 'not explicit'}\n"
            f"quoted_text: {self.quoted_text or 'none'}\n"
            f"explicit_application_intent: {str(self.explicit_application_intent).lower()}\n"
            f"correction: {str(self.correction).lower()}\n"
            f"context_reference: {self.context_reference or 'none'}\n"
            "Do not change an explicit value. Keep routing fields out of query text."
        )


@dataclass(frozen=True)
class ResolvedRequest:
    intent: Intent
    application_name: Optional[str] = None
    service: Optional[str] = None
    search_engine: Optional[str] = None
    query: Optional[str] = None
    profile_id: Optional[str] = None
    browser_name: Optional[str] = None
    target_tab_id: Optional[int] = None
    source_turn_id: Optional[str] = None
    raw_input: Optional[str] = None
    model_intent: Optional[str] = None
    locked: LockedConstraints = LockedConstraints()
    resolution_trace: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrowserExecutionEvidence:
    requested_profile_id: str
    requested_url: str
    target_tab_id: Optional[int] = None
    request_id: Optional[str] = None
    result_profile_id: Optional[str] = None
    result_url: Optional[str] = None
    result_tab_id: Optional[int] = None
    verified: bool = False
    error_code: Optional[str] = None


@dataclass(frozen=True)
class ActionRecord:
    request: ResolvedRequest
    result: ActionResultStatus
    visible_message: str
    browser_evidence: Optional[BrowserExecutionEvidence] = None
    timestamp: float = 0.0

    @classmethod
    def attempted(cls, request: ResolvedRequest) -> "ActionRecord":
        return cls(
            request=request,
            result=ActionResultStatus.ATTEMPTED,
            visible_message="",
            timestamp=time.monotonic(),
        )

    def rejected(self) -> "ActionRecord":
        return replace(
            self,
            result=ActionResultStatus.REJECTED,
            timestamp=time.monotonic(),
        )


@dataclass(frozen=True)
class ReusableActionTemplate:
    intent: Intent
    query: Optional[str] = None
    search_engine: Optional[str] = None
    service: Optional[str] = None
    profile_id: Optional[str] = None
    browser_name: Optional[str] = None
    application_name: Optional[str] = None

    @classmethod
    def from_request(cls, request: ResolvedRequest) -> "ReusableActionTemplate":
        return cls(
            intent=request.intent,
            query=request.query,
            search_engine=request.search_engine,
            service=request.service,
            profile_id=request.profile_id,
            browser_name=request.browser_name,
            application_name=request.application_name,
        )

    def resolve(
        self,
        *,
        turn_id: str,
        raw_input: str,
        constraints: LockedConstraints,
    ) -> ResolvedRequest:
        provider = constraints.search_engine or constraints.service
        intent = self.intent
        search_engine = self.search_engine
        service = self.service
        if provider == "youtube":
            intent = Intent.SEARCH_YOUTUBE if self.query else Intent.OPEN_SERVICE
            search_engine = "youtube" if self.query else None
            service = "youtube"
        elif provider in {"google", "bing", "duckduckgo"}:
            intent = Intent.SEARCH_WEB
            search_engine = provider
            service = provider
        return ResolvedRequest(
            intent=intent,
            application_name=self.application_name,
            service=service,
            search_engine=search_engine,
            query=self.query,
            profile_id=constraints.profile_id or self.profile_id,
            browser_name=self.browser_name,
            source_turn_id=turn_id,
            raw_input=raw_input,
            locked=constraints,
            resolution_trace=("cloned most recent verified action",),
        )


@dataclass(frozen=True)
class PendingMediaRequest:
    query: str
    service: Optional[str]
    profile_id: Optional[str]
    missing_fields: frozenset[str]
    proposed_values: tuple[tuple[str, object], ...]
    created_at: float
