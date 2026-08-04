"""Small, bounded session state; nothing is persisted between runs."""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PendingAction:
    tool_name: str
    arguments: Dict[str, object]
    description: str
    warning: str
    created_at: float


@dataclass
class ConversationState:
    history_limit: int
    pending_action_ttl: float = 60.0
    history: List[Dict[str, str]] = field(default_factory=list)
    pending_action: Optional[PendingAction] = None
    research_answer: Optional[str] = None
    research_sources: List[str] = field(default_factory=list)

    def add_exchange(self, user_text: str, assistant_text: str) -> None:
        self.history.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text[:2000]},
            ]
        )
        self.history = self.history[-self.history_limit :]

    def messages(self) -> List[Dict[str, str]]:
        return list(self.history)

    def set_pending(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, object]] = None,
        description: str = "",
        warning: str = "",
        created_at: Optional[float] = None,
    ) -> None:
        self.pending_action = PendingAction(
            tool_name=tool_name,
            arguments=dict(arguments or {}),
            description=description,
            warning=warning,
            created_at=time.monotonic() if created_at is None else created_at,
        )

    def clear_pending(self) -> None:
        self.pending_action = None

    def pending_expired(self, now: Optional[float] = None) -> bool:
        if self.pending_action is None:
            return False
        current = time.monotonic() if now is None else now
        return current - self.pending_action.created_at >= self.pending_action_ttl

    def clear_expired_pending(self, now: Optional[float] = None) -> Optional[PendingAction]:
        if not self.pending_expired(now):
            return None
        expired = self.pending_action
        self.pending_action = None
        return expired

    def store_research(self, answer: str, sources: List[str]) -> None:
        # A new research result predictably replaces the previous one.
        self.research_answer = answer
        self.research_sources = list(sources[:20])

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
