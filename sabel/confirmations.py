"""Conservative confirmation rules for destructive local actions."""

from typing import Optional


TRASH_WARNING = (
    "This will permanently delete everything currently in Trash.\n"
    "Would you like me to continue?"
)

TRASH_DESCRIPTION = (
    "Emptying Trash permanently deletes its current contents. SABEL pauses before "
    "destructive actions and waits for a clear confirmation so files are not "
    "deleted accidentally."
)

REDUNDANT_CONFIRMATION_FIELDS = {"action", "tool_name"}
UNCERTAIN_PENDING_RESPONSE = "uncertain"


def is_explicit_trash_confirmation(user_text: str) -> bool:
    """Apply a conservative deterministic guard after the model selects confirm."""
    lowered = " ".join(user_text.casefold().strip().split())
    cancellation = (
        lowered in {
            "no",
            "no.",
            "cancel",
            "cancel.",
            "stop",
            "stop.",
            "stop action",
            "stop action.",
            "never mind",
            "never mind.",
            "forget it",
            "forget it.",
        }
        or "do not " in lowered
        or "don't " in lowered
    )
    if cancellation:
        return False
    if lowered in {"okay", "okay.", "maybe", "maybe.", "sure", "sure."}:
        return False
    return (
        lowered.startswith("yes")
        or "i confirm" in lowered
        or "go ahead" in lowered
        or "proceed" in lowered
        or "permanently delete" in lowered
    )


def classify_pending_response(user_text: str) -> Optional[str]:
    """Provide a narrow safety backstop for clear pending-action language."""
    lowered = " ".join(user_text.casefold().strip().split())
    if (
        lowered in {
            "no",
            "no.",
            "cancel",
            "cancel.",
            "stop",
            "stop.",
            "stop action",
            "stop action.",
            "never mind",
            "never mind.",
            "forget it",
            "forget it.",
        }
        or "do not " in lowered
        or "don't " in lowered
    ):
        return "cancel_pending_action"
    if is_explicit_trash_confirmation(user_text):
        return "confirm_pending_action"
    if lowered in {"okay", "okay.", "maybe", "maybe.", "sure", "sure."}:
        return UNCERTAIN_PENDING_RESPONSE

    question_openers = (
        "why ",
        "what does ",
        "what do you mean",
        "what will ",
        "is this ",
        "is it ",
        "how does ",
        "explain ",
    )
    pending_references = (
        "trash",
        "delete",
        "empty",
        "permanent",
        "confirm",
        "that mean",
        "what do you mean",
        "happen",
        "remove it",
        "pending action",
        "previous message",
    )
    if (lowered.startswith(question_openers) or lowered.endswith("?")) and any(
        reference in lowered for reference in pending_references
    ):
        return "explain_pending_action"
    return None


def normalize_confirmation_arguments(
    arguments: object, pending_tool_name: str
) -> Optional[dict[str, object]]:
    """Accept only empty args or documented redundant identity fields that match."""
    if not isinstance(arguments, dict):
        return None
    if not arguments:
        return {}
    if not set(arguments).issubset(REDUNDANT_CONFIRMATION_FIELDS):
        return None
    if any(value != pending_tool_name for value in arguments.values()):
        return None
    return {}
