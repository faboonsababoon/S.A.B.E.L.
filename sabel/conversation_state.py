"""Small, bounded session state; nothing is persisted between runs."""

from dataclasses import dataclass, field
import time
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PendingDestructiveAction:
    tool_name: str
    arguments: Dict[str, object]
    description: str
    warning: str
    created_at: float


@dataclass(frozen=True)
class PendingClarification:
    original_request: str
    question: str
    intent: str
    collected_slots: Dict[str, object]
    missing_slots: List[str]
    created_at: float
    proposed_slots: Dict[str, object] = field(default_factory=dict)

    @property
    def expected_slot(self) -> Optional[str]:
        """Compatibility view for older callers; state remains slot-structured."""
        return self.missing_slots[0] if self.missing_slots else None


@dataclass(frozen=True)
class PendingCloudFallback:
    original_request: str
    task: str
    requires_current_web_information: bool
    created_at: float


@dataclass(frozen=True)
class ActionResultRecord:
    tool_name: str
    success: bool
    message: str
    created_at: float


@dataclass
class ConversationState:
    history_limit: int
    pending_action_ttl: float = 60.0
    clarification_ttl: float = 60.0
    pending_destructive_action: Optional[PendingDestructiveAction] = None
    pending_clarification: Optional[PendingClarification] = None
    pending_cloud_fallback: Optional[PendingCloudFallback] = None
    recent_history: List[Dict[str, str]] = field(default_factory=list)
    last_action_result: Optional[ActionResultRecord] = None
    last_browser_service: Optional[str] = None
    last_browser_profile: Optional[str] = None
    last_browser_query: Optional[str] = None
    research_answer: Optional[str] = None
    research_sources: List[str] = field(default_factory=list)
    last_unverified_local_fallback_task: Optional[str] = None

    def add_exchange(self, user_text: str, assistant_text: str) -> None:
        self.recent_history.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text[:2000]},
            ]
        )
        self.recent_history = self.recent_history[-self.history_limit :]

    def messages(self) -> List[Dict[str, str]]:
        return list(self.recent_history)

    def set_pending_destructive_action(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, object]] = None,
        description: str = "",
        warning: str = "",
        created_at: Optional[float] = None,
    ) -> None:
        self.pending_destructive_action = PendingDestructiveAction(
            tool_name=tool_name,
            arguments=dict(arguments or {}),
            description=description,
            warning=warning,
            created_at=time.monotonic() if created_at is None else created_at,
        )

    def clear_pending_destructive_action(self) -> None:
        self.pending_destructive_action = None

    def pending_expired(self, now: Optional[float] = None) -> bool:
        if self.pending_destructive_action is None:
            return False
        current = time.monotonic() if now is None else now
        return (
            current - self.pending_destructive_action.created_at
            >= self.pending_action_ttl
        )

    def clear_expired_pending_destructive_action(
        self, now: Optional[float] = None
    ) -> Optional[PendingDestructiveAction]:
        if not self.pending_expired(now):
            return None
        expired = self.pending_destructive_action
        self.pending_destructive_action = None
        return expired

    def set_pending_clarification(
        self,
        original_request: str,
        question: str,
        expected_slot: Optional[str] = None,
        created_at: Optional[float] = None,
        *,
        intent: str = "",
        collected_slots: Optional[Dict[str, object]] = None,
        missing_slots: Optional[List[str]] = None,
        proposed_slots: Optional[Dict[str, object]] = None,
    ) -> None:
        unresolved = list(missing_slots or ([expected_slot] if expected_slot else []))
        self.pending_clarification = PendingClarification(
            original_request=original_request,
            question=question,
            intent=intent,
            collected_slots=dict(collected_slots or {}),
            missing_slots=unresolved,
            created_at=time.monotonic() if created_at is None else created_at,
            proposed_slots=dict(proposed_slots or {}),
        )

    def clear_pending_clarification(self) -> None:
        self.pending_clarification = None

    def clarification_expired(self, now: Optional[float] = None) -> bool:
        if self.pending_clarification is None:
            return False
        current = time.monotonic() if now is None else now
        return (
            current - self.pending_clarification.created_at
            >= self.clarification_ttl
        )

    def clear_expired_clarification(
        self, now: Optional[float] = None
    ) -> Optional[PendingClarification]:
        if not self.clarification_expired(now):
            return None
        expired = self.pending_clarification
        self.pending_clarification = None
        return expired

    def record_action_result(
        self, tool_name: str, success: bool, message: str
    ) -> None:
        self.last_action_result = ActionResultRecord(
            tool_name=tool_name,
            success=success,
            message=message[:1000],
            created_at=time.monotonic(),
        )

    def action_context(self) -> str:
        if self.last_action_result is None:
            return "No local action result is available."
        status = "succeeded" if self.last_action_result.success else "failed"
        return (
            f"Last local action {status}. Visible result: "
            f"{self.last_action_result.message}"
        )

    def record_browser_context(
        self,
        service: Optional[str],
        profile: Optional[str],
        query: Optional[str] = None,
    ) -> None:
        """Remember only validated browser slots for natural follow-up requests."""
        self.last_browser_service = service
        self.last_browser_profile = profile
        if query is not None:
            self.last_browser_query = query

    def clear_all_pending(self) -> None:
        self.pending_destructive_action = None
        self.pending_clarification = None
        self.pending_cloud_fallback = None

    def set_pending_cloud_fallback(
        self,
        original_request: str,
        task: str,
        requires_current_web_information: bool,
        created_at: Optional[float] = None,
    ) -> None:
        self.pending_cloud_fallback = PendingCloudFallback(
            original_request=original_request,
            task=task,
            requires_current_web_information=requires_current_web_information,
            created_at=time.monotonic() if created_at is None else created_at,
        )

    def clear_pending_cloud_fallback(self) -> None:
        self.pending_cloud_fallback = None

    def cloud_fallback_expired(self, now: Optional[float] = None) -> bool:
        if self.pending_cloud_fallback is None:
            return False
        current = time.monotonic() if now is None else now
        return current - self.pending_cloud_fallback.created_at >= self.clarification_ttl

    def clear_expired_cloud_fallback(
        self, now: Optional[float] = None
    ) -> Optional[PendingCloudFallback]:
        if not self.cloud_fallback_expired(now):
            return None
        expired = self.pending_cloud_fallback
        self.pending_cloud_fallback = None
        return expired

    def store_research(self, answer: str, sources: List[str]) -> None:
        # A new research result predictably replaces the previous one.
        self.research_answer = answer
        self.research_sources = list(sources[:20])
        self.last_unverified_local_fallback_task = None

    def resolve_source(self, one_based_index: int) -> Tuple[Optional[str], str]:
        if one_based_index < 1 or one_based_index > len(self.research_sources):
            return None, "That source number is not available in the latest research."
        return self.research_sources[one_based_index - 1], ""

    def research_context(self) -> str:
        if not self.research_answer:
            return "No cloud research result is stored."
        return (
            "Latest research excerpt:\n"
            f"{self.research_answer[:2000]}\n"
            f"Stored numbered sources: {len(self.research_sources)}"
        )
