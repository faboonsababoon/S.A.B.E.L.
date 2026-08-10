"""Typed browser bridge, profile, action, and task models."""

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Optional


class BrowserActionState(Enum):
    PLANNED = "planned"
    REQUESTED = "requested"
    EXECUTED = "executed"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class BrowserResult:
    state: BrowserActionState
    success: bool
    verified: bool = False
    result: dict[str, object] = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None
    request_id: Optional[str] = None
    profile_id: Optional[str] = None
    connection_instance_id: Optional[str] = None

    @classmethod
    def failed(cls, message: str, code: str) -> "BrowserResult":
        return cls(
            BrowserActionState.FAILED,
            False,
            error=message,
            error_code=code,
        )

    @classmethod
    def cancelled(cls, message: str = "Browser task cancelled.") -> "BrowserResult":
        return cls(
            BrowserActionState.CANCELLED,
            False,
            error=message,
            error_code="CANCELLED",
        )


@dataclass(frozen=True)
class BrowserProfile:
    profile_id: str
    profile_name: str
    instance_id: str
    extension_version: str
    connected_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class BrowserSearchRequest:
    """One validated search plan with routing fields kept independent."""

    profile_id: str
    search_engine: str
    query: str
    target_tab_id: Optional[int] = None


@dataclass
class ProfileBrowserState:
    """Verified browser context belonging to exactly one Chrome profile."""

    profile_id: str
    active_tab_id: Optional[int] = None
    last_service: Optional[str] = None
    last_search_engine: Optional[str] = None
    last_search_query: Optional[str] = None
    last_successful_action: Optional[str] = None
    last_url: Optional[str] = None


@dataclass(frozen=True)
class LastBrowserReference:
    """Most recent verified browser action available to conversational references."""

    profile_id: str
    service: Optional[str]
    search_engine: Optional[str]
    tab_id: Optional[int]
    completed_at: float


@dataclass(frozen=True)
class BrowserActionContext:
    """Safe structured evidence carried from execution to session state/debug output."""

    profile_id: str
    service: Optional[str] = None
    search_engine: Optional[str] = None
    query: Optional[str] = None
    tab_id: Optional[int] = None
    url: Optional[str] = None
    connection_instance_id: Optional[str] = None


class BrowserTaskStatus(Enum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STEP_LIMIT_REACHED = "step_limit_reached"
    CLARIFICATION_REQUIRED = "clarification_required"


class BrowserDecisionAction(Enum):
    """Planner vocabulary; none of these values execute by themselves."""

    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    PRESS_KEY = "press_key"
    NAVIGATE = "navigate"
    BACK = "back"
    OPEN_TAB = "open_tab"
    DONE = "done"
    CLARIFY = "clarify"
    REQUEST_CONFIRMATION = "request_confirmation"


@dataclass(frozen=True)
class BrowserDecision:
    """One strictly parsed planner decision for one current snapshot."""

    action: BrowserDecisionAction
    reason: str
    expected_result: str = ""
    element_ref: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    value: Optional[str] = None
    key: Optional[str] = None
    delta_y: Optional[int] = None
    answer: Optional[str] = None
    evidence: tuple[str, ...] = ()
    question: Optional[str] = None
    requested_action: Optional[BrowserDecisionAction] = None


@dataclass(frozen=True)
class PendingBrowserAction:
    """Python-owned browser proposal paused before a consequential effect."""

    decision: BrowserDecision
    snapshot: dict[str, object]
    prompt: str
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ScopeExpansion:
    instruction: str
    added_domains: frozenset[str] = frozenset()
    added_actions: frozenset[str] = frozenset()
    added_profiles: frozenset[str] = frozenset()
    approved_at: float = field(default_factory=time.time)


@dataclass
class BrowserTask:
    task_id: str
    original_user_request: str
    objective: str
    profile_id: str
    allowed_domains: set[str]
    allowed_actions: set[str]
    allowed_profiles: set[str]
    initial_url: Optional[str] = None
    initial_service: Optional[str] = None
    user_approved_expansions: list[ScopeExpansion] = field(default_factory=list)
    current_tab_id: Optional[int] = None
    step_count: int = 0
    max_steps: int = 15
    status: BrowserTaskStatus = BrowserTaskStatus.PLANNED
    created_at: float = field(default_factory=time.time)
    last_snapshot_id: Optional[str] = None
    last_url: Optional[str] = None
    recent_actions: list[str] = field(default_factory=list)
    last_action_signature: Optional[str] = None
    last_snapshot_fingerprint: Optional[str] = None
    no_progress_count: int = 0
    max_no_progress: int = 2
    pending_action: Optional[PendingBrowserAction] = None

    def expand_from_user(self, expansion: ScopeExpansion) -> None:
        self.allowed_domains.update(expansion.added_domains)
        self.allowed_actions.update(expansion.added_actions)
        self.allowed_profiles.update(expansion.added_profiles)
        self.user_approved_expansions.append(expansion)


@dataclass(frozen=True)
class BrowserAuditEntry:
    timestamp: float
    task_id: str
    profile_id: str
    domain: str
    action: str
    result: str
    confirmation_status: str
    policy_decision: str
