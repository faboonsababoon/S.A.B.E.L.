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
        "show_browser_profiles",
        "List the authenticated SABEL Chrome profile connections. Use for browser-profile status, not general SABEL status.",
    ),
    _tool(
        "show_browser_tabs",
        "List friendly tab titles and domains in one connected Personal or NYU Chrome profile.",
        {"profile": {"type": "string"}},
        ["profile"],
    ),
    _tool(
        "open_service",
        "Open a reviewed service in one exact connected Chrome profile. Python resolves the URL; never invent one.",
        {
            "service_name": {"type": "string"},
            "profile_id": {"type": "string", "enum": ["personal", "nyu"]},
        },
        ["service_name", "profile_id"],
    ),
    _tool(
        "search_web",
        "Search Google, Bing, or DuckDuckGo in one exact connected Chrome profile. Keep the literal query separate from engine and profile routing.",
        {
            "query": {"type": "string"},
            "search_engine": {"type": "string", "enum": ["google", "bing", "duckduckgo", "default"]},
            "profile_id": {"type": "string", "enum": ["personal", "nyu"]},
        },
        ["query", "search_engine", "profile_id"],
    ),
    _tool(
        "search_youtube",
        "Search YouTube in one exact connected Chrome profile. Keep the literal query separate from profile routing.",
        {
            "query": {"type": "string"},
            "profile_id": {"type": "string", "enum": ["personal", "nyu"]},
        },
        ["query", "profile_id"],
    ),
    _tool(
        "browser_copilot_task",
        "Run a bounded multi-step browser task that must inspect and navigate a page. Do not use for a simple open or search. Provide either one reviewed service or an explicit user-supplied URL, never both.",
        {
            "objective": {"type": "string"},
            "service": {"type": "string"},
            "profile": {"type": "string"},
            "initial_url": {"type": "string"},
        },
        ["objective", "profile"],
    ),
    _tool(
        "stop_browser_task",
        "Immediately stop SABEL browser control without closing the user's manually managed tabs.",
    ),
    _tool(
        "show_recent_browser_actions",
        "Show a concise bounded security audit summary of recent browser actions.",
    ),
    _tool(
        "open_website",
        "Open an explicit HTTP/HTTPS URL supplied by the user. Do not use this for a registered service name and do not invent URLs.",
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
        "check_application_installed",
        "Authoritatively check SABEL's local macOS application catalog for one application. Never guess whether software is installed.",
        {"application_name": {"type": "string"}},
        ["application_name"],
    ),
    _tool(
        "show_installed_applications",
        "Show a bounded, path-free list from SABEL's actual local macOS application catalog.",
    ),
    _tool(
        "open_youtube_search",
        "Open a YouTube search for a creator, channel, video, or topic when no exact URL was supplied. Do not use this merely to open the YouTube homepage. Never invent a channel URL.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "open_spotify_search",
        "Open Spotify search results for a track, artist, album, or playlist. This opens results and does not claim playback succeeded.",
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
        "get_trash_status",
        "Read only: inspect whether the current user's macOS Trash contains items. This is not SABEL runtime status.",
    ),
    _tool(
        "show_capabilities",
        "Show SABEL's supported capabilities and cloud delegation. Always use for questions about what SABEL can do, its commands, or its abilities; never answer those from memory.",
    ),
    _tool(
        "show_status",
        "Show SABEL runtime configuration: model provider, model availability, cloud mode, and cloud configuration. Never use for Trash contents.",
    ),
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
    _tool(
        "request_clarification",
        "Ask one necessary question only when a requested action is ambiguous or missing a required target or argument. Never use for greetings or casual conversation.",
        {
            "question": {"type": "string"},
            "expected_slot": {"type": "string"},
        },
        ["question"],
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


PENDING_CLARIFICATION_TOOLS = [
    _tool(
        "answer_clarification",
        "The user answered the pending clarification. Pass exactly {}.",
    ),
    _tool(
        "cancel_clarification",
        "The user cancelled or abandoned the pending request. Pass exactly {}.",
    ),
    _tool(
        "question_about_clarification",
        "The user asks what the clarification means or why it is needed. Pass exactly {}.",
    ),
    _tool(
        "route_new_request",
        "The user gave an unrelated new request that replaces the clarification. Pass exactly {}.",
    ),
]


LOCAL_SYSTEM_PROMPT = """You are SABEL, a concise local macOS assistant and tool router.
Not every message requires a tool. Answer greetings, acknowledgements, explanations, thanks, and casual conversation directly in natural text.
When a supported tool matches the requested action, you MUST return its native tool call. Do not narrate, promise, or describe the action instead. Never write an internal tool name or JSON as prose.
For every tool whose schema has no properties, pass exactly an empty arguments object {}.
Use request_clarification only when an action cannot run because a required target or argument is missing. Never ask meta-questions such as how the user wants you to respond.
If a requested action such as playing media is ambiguous and no exact supported action is clear, ask one concrete clarification instead of guessing or refusing generically.
Use get_trash_status only for Trash contents. Use show_status only for SABEL runtime configuration.
Use check_application_installed for questions about whether a named application is installed, and show_installed_applications for requests to list installed applications. Never answer installed-software questions from memory.
Use show_browser_profiles for connected Chrome profiles, stop_browser_task to stop browser control, and show_recent_browser_actions for the local browser audit summary.
Use open_web_search for raw browser results and delegate_to_openai for current research, comparison, recommendation, or synthesis.
For browser work, keep profile, provider, query, service, and tab context in separate fields. Profile and provider instructions are never part of a search query. Explicit values in the current user message override prior context. Google means web search unless the user explicitly says Google Chrome. YouTube means YouTube search when a query is present. Never reuse an old query or a previously used profile when the user names a new value.
Use open_service for a one-step registered-service homepage. Use browser_copilot_task only when the user asks SABEL to inspect and navigate a page over multiple steps; provide either a reviewed service or an explicit URL from the user's request. Use search_web for a one-step Google/Bing/DuckDuckGo search, search_youtube for one-step YouTube results, open_website only for a one-step explicit URL, and open_spotify_search whenever the user asks to open, find, search for, or play named content on Spotify.
Use show_status for requests about SABEL's health or runtime status, including natural phrasings such as "show your status", "what is your status", "are you working normally", and "show SABEL status". Python generates the values.
Examples: YouTube homepage -> open_service with service_name "youtube" and profile_id "personal"; Google search in NYU -> search_web with a clean query, search_engine "google", and profile_id "nyu"; YouTube search in Personal -> search_youtube with a clean query and profile_id "personal"; an explicit youtube.com URL -> open_website; emptying Trash -> empty_trash {}; inspecting Trash contents -> get_trash_status {}; a greeting -> normal friendly text; an ambiguous play request -> request_clarification with one concrete question.
Never invent abilities, tools, shell commands, or hidden results. Do not reveal reasoning."""


PENDING_SYSTEM_PROMPT = """You interpret replies while one consequential action is pending.
Return exactly one native tool call with an empty arguments object.
Use confirm_pending_action for a clear contextual confirmation such as yes/do it, go ahead, proceed, I confirm, or permanently delete it.
Use cancel_pending_action for no, cancel, never mind, stop, or instructions not to perform it.
Use explain_pending_action for questions about the warning, action, permanence, confirmation, or previous SABEL message.
Use route_new_request for an unrelated command that should replace the pending action.
Bare okay, maybe, or sure is uncertain: ask for a clearer confirmation in ordinary prose and keep the action pending.
Never regenerate the pending action name or arguments. Do not reveal reasoning."""


PENDING_CLARIFICATION_SYSTEM_PROMPT = """Classify one reply to a pending clarification.
Return exactly one native zero-argument tool call.
Use answer_clarification when the reply supplies the requested information.
Use cancel_clarification for cancel, stop, never mind, forget it, or do not do that.
Use question_about_clarification when the user asks what the question means or why it is needed.
Use route_new_request for an unrelated new command.
Do not repeat the clarification question and do not reveal reasoning."""
