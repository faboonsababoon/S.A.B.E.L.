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


class BrowserTaskStatus(Enum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STEP_LIMIT_REACHED = "step_limit_reached"


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
    user_approved_expansions: list[ScopeExpansion] = field(default_factory=list)
    current_tab_id: Optional[int] = None
    step_count: int = 0
    max_steps: int = 8
    status: BrowserTaskStatus = BrowserTaskStatus.PLANNED
    created_at: float = field(default_factory=time.time)
    last_snapshot_id: Optional[str] = None
    last_url: Optional[str] = None

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
