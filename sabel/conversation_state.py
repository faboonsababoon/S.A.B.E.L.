"""Small, bounded session state; nothing is persisted between runs."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PendingAction:
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationState:
    history_limit: int
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

    def set_pending(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> None:
        self.pending_action = PendingAction(name, arguments or {})

    def clear_pending(self) -> None:
        self.pending_action = None

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

