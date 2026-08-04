"""Native Ollama function schemas: the complete local tool allowlist."""

from typing import Any, Dict, List


def _tool(name: str, description: str, properties=None, required=None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


LOCAL_TOOLS: List[Dict[str, Any]] = [
    _tool(
        "open_website",
        "Open a specific HTTP/HTTPS website or the homepage of a well-known named site. Infer the canonical public homepage URL when unambiguous. If the only target is YouTube, call this with https://www.youtube.com.",
        {"url": {"type": "string"}},
        ["url"],
    ),
    _tool(
        "open_application",
        "Open an installed macOS application by name.",
        {"application_name": {"type": "string"}},
        ["application_name"],
    ),
    _tool(
        "open_youtube_search",
        "Open a YouTube search for a creator, channel, video, or topic when no exact URL was supplied. Do not use this merely to open the YouTube homepage. Never invent a channel URL.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "open_web_search",
        "Only open ordinary browser search results when the user asks to search, look up, or Google something without asking SABEL to research, compare, recommend, synthesize, or explain the results.",
        {
            "query": {"type": "string"},
            "search_engine": {"type": "string"},
        },
        ["query"],
    ),
    _tool("empty_trash", "Request permanent deletion of current Trash contents; Python will require confirmation."),
    _tool(
        "show_capabilities",
        "Show SABEL's supported capabilities and cloud delegation. Always use for questions about what SABEL can do, its commands, or its abilities; never answer those from memory.",
    ),
    _tool("show_status", "Show local provider, model availability, cloud mode, and cloud configuration."),
    _tool("exit_assistant", "Close SABEL cleanly."),
    _tool(
        "delegate_to_openai",
        "Highest-priority route for requests asking SABEL to research and explain, compare, recommend, or synthesize current information. Return this delegation request instead of open_web_search even when a browser could find raw links.",
        {
            "task": {"type": "string"},
            "reason": {"type": "string"},
            "requires_current_web_information": {"type": "boolean"},
        },
        ["task", "reason", "requires_current_web_information"],
    ),
    _tool(
        "open_research_source",
        "Open a numbered source URL from the latest stored cloud research result.",
        {"source_number": {"type": "integer", "minimum": 1}},
        ["source_number"],
    ),
]


PENDING_CONFIRMATION_TOOLS = [
    _tool(
        "confirm_pending_action",
        "Confirm the stored pending action after a clear contextual affirmation. Pass exactly {} because Python already owns the action and arguments.",
    ),
    _tool(
        "cancel_pending_action",
        "Cancel the stored pending action after a refusal or cancellation. Pass exactly {}.",
    ),
    _tool(
        "explain_pending_action",
        "Explain the warning, permanence, or reason confirmation is required. Pass exactly {} and keep the action pending.",
    ),
    _tool(
        "route_new_request",
        "The message is an unrelated new command, not confirmation, cancellation, or a question about the pending action. Pass exactly {}.",
    ),
]


LOCAL_SYSTEM_PROMPT = """You are SABEL's fast local macOS router.
Your only valid outputs are one native tool call or one brief clarification question.
When a provided tool fits, you MUST call exactly one tool; never answer it in ordinary prose.
Never invent abilities, tools, or shell commands.
For a tool whose schema has no properties, pass exactly an empty arguments object.
Routing precedence:
1. Asking SABEL to research/explain, compare, recommend, or synthesize current results -> delegate_to_openai.
2. Asking only to search/look up/Google -> open_web_search.
3. Asking to open an unambiguous named site's homepage -> open_website (YouTube is https://www.youtube.com). A bare "open + well-known site" request is complete, never ambiguous, and must not become a search. Absence of a creator, video, topic, or query means homepage; do not ask homepage-versus-search.
4. Asking for a named creator/channel/video on YouTube without an exact URL -> open_youtube_search without clarification.
Return one brief clarification only when intent or a required target is genuinely unclear.
Do not reveal reasoning."""


PENDING_SYSTEM_PROMPT = """You interpret replies while one destructive action is pending.
Return exactly one native tool call with an empty arguments object.
Use confirm_pending_action for a clear contextual confirmation such as yes/do it, go ahead, proceed, I confirm, or permanently delete it.
Use cancel_pending_action for no, cancel, never mind, stop, or instructions not to delete.
Use explain_pending_action for questions about the warning, action, permanence, confirmation, or previous SABEL message.
Use route_new_request for an unrelated command that should replace the pending action.
Bare okay, maybe, or sure is uncertain: ask for a clearer confirmation in ordinary prose and keep the action pending.
Never regenerate the pending action name or arguments. Do not reveal reasoning."""
