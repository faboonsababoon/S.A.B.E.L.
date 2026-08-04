"""Validate targets and perform SABEL's approved macOS actions."""

from dataclasses import dataclass
import subprocess
from typing import Callable, Sequence, Union
from urllib.parse import urlencode, urlsplit


@dataclass(frozen=True)
class ActionResult:
    """The user-facing outcome of an attempted action."""

    success: bool
    message: str


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def normalize_website(address: str) -> str:
    """Return a valid HTTP(S) URL, adding HTTPS when no scheme is given."""
    candidate = address.strip()
    if not candidate:
        raise ValueError("Please provide a website address.")

    # Whitespace and control characters make an address ambiguous or malformed.
    if any(character.isspace() or ord(character) < 32 for character in candidate):
        raise ValueError("That website address is malformed.")
    if any(token in candidate for token in (";", "|", "`", "$(`", ">", "<")):
        raise ValueError("That website address contains unsupported command syntax.")

    parsed_input = urlsplit(candidate)
    if parsed_input.scheme:
        if parsed_input.scheme.casefold() not in {"http", "https"}:
            raise ValueError("Only http:// and https:// website addresses are supported.")
        normalized = candidate
    else:
        normalized = f"https://{candidate}"

    parsed = urlsplit(normalized)
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("That website address is malformed.")

    # Accessing port makes urllib validate invalid values such as :not-a-port.
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("That website address has an invalid port.") from error

    return normalized


def _run_process(
    arguments: Sequence[str], run_command: RunCommand
) -> Union[ActionResult, subprocess.CompletedProcess[str]]:
    """Run one reviewed macOS process and translate expected OS failures."""
    try:
        return run_command(
            list(arguments),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ActionResult(False, "The required macOS command could not be found.")
    except OSError as error:
        return ActionResult(False, f"macOS could not perform that action: {error}")


def open_website(
    address: str, run_command: RunCommand = subprocess.run
) -> ActionResult:
    """Validate and open a website in the default browser."""
    try:
        url = normalize_website(address)
    except ValueError as error:
        return ActionResult(False, str(error))

    completed = _run_process(["open", url], run_command)
    if isinstance(completed, ActionResult):
        return completed
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown macOS error"
        return ActionResult(False, f"The website could not be opened: {detail}")
    return ActionResult(True, f"Opening {url}")


def open_application(
    application_name: str, run_command: RunCommand = subprocess.run
) -> ActionResult:
    """Open a named application using macOS's standard 'open -a' command."""
    name = application_name.strip()
    if not name:
        return ActionResult(False, "Please provide an application name.")

    completed = _run_process(["open", "-a", name], run_command)
    if isinstance(completed, ActionResult):
        return completed
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "application not found"
        return ActionResult(False, f"The application could not be opened: {detail}")
    return ActionResult(True, f"Opening {name}")


def open_youtube_search(
    query: str, run_command: RunCommand = subprocess.run
) -> ActionResult:
    """Open a safely URL-encoded YouTube search."""
    cleaned = query.strip()
    if not cleaned:
        return ActionResult(False, "Please provide a YouTube search query.")
    url = "https://www.youtube.com/results?" + urlencode({"search_query": cleaned})
    return open_website(url, run_command=run_command)


def open_web_search(
    query: str,
    search_engine: str = "google",
    run_command: RunCommand = subprocess.run,
) -> ActionResult:
    """Open a normal browser search using a small reviewed engine allowlist."""
    cleaned = query.strip()
    if not cleaned:
        return ActionResult(False, "Please provide a web search query.")
    engines = {
        "google": ("https://www.google.com/search", "q"),
        "bing": ("https://www.bing.com/search", "q"),
        "duckduckgo": ("https://duckduckgo.com/", "q"),
    }
    selected = search_engine.strip().casefold() or "google"
    if selected not in engines:
        return ActionResult(False, "Supported search engines are Google, Bing, and DuckDuckGo.")
    base_url, parameter = engines[selected]
    return open_website(
        base_url + "?" + urlencode({parameter: cleaned}),
        run_command=run_command,
    )


def empty_trash(run_command: RunCommand = subprocess.run) -> ActionResult:
    """Empty Trash using one fixed, reviewed Finder command after confirmation."""
    script = 'tell application "Finder" to empty trash'
    completed = _run_process(["osascript", "-e", script], run_command)
    if isinstance(completed, ActionResult):
        return completed
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown macOS error"
        return ActionResult(False, f"Trash could not be emptied: {detail}")
    return ActionResult(True, "Trash emptied successfully.")
