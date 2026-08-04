"""The single boundary that permits text to become a visible SABEL response."""

import re
from typing import Iterable


SAFE_RENDERING_ERROR = (
    "I could not safely interpret that request. Please try rephrasing it."
)


def render_visible_response(message: object, internal_names: Iterable[str]) -> str:
    """Render only plain user-facing text, never internal protocol values."""
    if not isinstance(message, str):
        return SAFE_RENDERING_ERROR
    cleaned = message.strip()
    if not cleaned:
        return "I could not produce a response."

    names = tuple(sorted({name.casefold() for name in internal_names}, key=len, reverse=True))
    if names:
        internal_pattern = r"(?<![a-z0-9_])(?:" + "|".join(
            re.escape(name) for name in names
        ) + r")(?![a-z0-9_])"
        if re.search(internal_pattern, cleaned.casefold()):
            return SAFE_RENDERING_ERROR

    unsafe_representations = (
        r"^\s*\{[\s\S]*\}\s*$",
        r"\{\s*[\"'][A-Za-z0-9_ -]+[\"']\s*:",
        r"\b(?:ToolCall|LocalToolCall|ActionResult|RouterResult|BrowserResult|BrowserOutcome)\s*\(",
        r"\bRouterResultType\.[A-Z_]+\b",
        r"<[^>]+ object at 0x[0-9a-f]+>",
        r"/(?:Applications|System/Applications|System/Library/CoreServices)/[^\n]+\.app\b",
    )
    if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in unsafe_representations):
        return SAFE_RENDERING_ERROR
    return cleaned
