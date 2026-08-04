"""Validate targets and perform SABEL's approved macOS actions."""

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Callable, List, Sequence, Union
from urllib.parse import quote, urlencode, urlsplit

from sabel.services import (
    LEGACY_OPEN_SERVICE_NAMES,
    SERVICE_REGISTRY,
    normalize_service_name,
    resolve_service,
)


@dataclass(frozen=True)
class ActionResult:
    """The user-facing outcome of an attempted action."""

    success: bool
    message: str


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
OpenDirectory = Callable[[str, int], int]
ListDirectory = Callable[[int], List[str]]
CloseDirectory = Callable[[int], None]


APPLICATION_ALIASES = {
    "safari": "Safari",
    "spotify": "Spotify",
    "roblox": "Roblox",
    "settings": "System Settings",
    "system settings": "System Settings",
}
ALLOWED_APPLICATION_PATH_ROOTS = (
    PurePosixPath("/Applications"),
    PurePosixPath("/System/Applications"),
    PurePosixPath("/System/Library/CoreServices"),
)


def normalize_application_name(value: str) -> str:
    """Validate an app name and safely reduce allowlisted .app paths to a name.

    SABEL never executes the supplied path. A canonical path beneath one of the
    reviewed macOS application roots may be reduced to its final ``.app`` name;
    every other path is rejected.
    """
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ValueError("Please provide an application name.")
    if any(ord(character) < 32 for character in cleaned):
        raise ValueError("That application name is malformed.")
    if any(token in cleaned for token in (";", "|", "`", "$(`", ">", "<", "\n")):
        raise ValueError("That application name contains unsupported command syntax.")

    if "/" in cleaned or "\\" in cleaned:
        path = PurePosixPath(cleaned)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.suffix.casefold() != ".app"
            or not any(path.is_relative_to(root) for root in ALLOWED_APPLICATION_PATH_ROOTS)
        ):
            raise ValueError("Application paths are not accepted; use an application name.")
        cleaned = path.stem

    if not re.fullmatch(r"[\w][\w .+&'()-]{0,127}", cleaned, re.UNICODE):
        raise ValueError("That application name is malformed.")
    return APPLICATION_ALIASES.get(cleaned.casefold(), cleaned)


def application_mentioned(text: str) -> Union[str, None]:
    """Resolve reviewed aliases from original user text for router recovery."""
    folded = text.casefold()
    for alias in sorted(APPLICATION_ALIASES, key=len, reverse=True):
        if re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", folded):
            return APPLICATION_ALIASES[alias]
    return None


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
    try:
        name = normalize_application_name(application_name)
    except ValueError as error:
        return ActionResult(False, str(error))

    completed = _run_process(["open", "-a", name], run_command)
    if isinstance(completed, ActionResult):
        return completed
    if completed.returncode != 0:
        return ActionResult(False, f"I could not find an application named {name}.")
    return ActionResult(True, f"Opening {name}.")


def open_service(
    service_name: str,
    browser: str = "",
    run_command: RunCommand = subprocess.run,
) -> ActionResult:
    """Resolve a reviewed service and open its canonical URL."""
    try:
        normalized_name = normalize_service_name(service_name)
    except ValueError:
        normalized_name = ""
    if normalized_name not in LEGACY_OPEN_SERVICE_NAMES:
        return ActionResult(
            False,
            "I do not recognize that service. Try YouTube, Google, or GitHub.",
        )
    service = resolve_service(service_name)
    if service is None or not service.url:
        supported = ", ".join(
            item.display_name for item in SERVICE_REGISTRY.values() if item.url
        )
        return ActionResult(
            False,
            f"I do not recognize that service. Supported services are {supported}.",
        )
    arguments = ["open"]
    if browser.strip():
        try:
            browser_name = normalize_application_name(browser)
        except ValueError as error:
            return ActionResult(False, str(error))
        arguments.extend(["-a", browser_name])
    arguments.append(service.url)
    completed = _run_process(arguments, run_command)
    if isinstance(completed, ActionResult):
        return completed
    if completed.returncode != 0:
        return ActionResult(False, f"I could not open {service.display_name}.")
    return ActionResult(True, f"Opening {service.display_name}.")


def open_spotify_search(
    query: str, run_command: RunCommand = subprocess.run
) -> ActionResult:
    """Open a fixed Spotify search URI, falling back to its HTTPS search page."""
    cleaned = " ".join(query.strip().split())
    if not cleaned:
        return ActionResult(False, "Please provide a Spotify search query.")
    if any(ord(character) < 32 for character in cleaned):
        return ActionResult(False, "That Spotify search query is malformed.")

    spotify_uri = "spotify:search:" + quote(cleaned, safe="")
    completed = _run_process(["open", "-a", "Spotify", spotify_uri], run_command)
    if isinstance(completed, ActionResult):
        return completed
    if completed.returncode != 0:
        web_url = "https://open.spotify.com/search/" + quote(cleaned, safe="")
        fallback = _run_process(["open", web_url], run_command)
        if isinstance(fallback, ActionResult):
            return fallback
        if fallback.returncode != 0:
            return ActionResult(False, "I could not open Spotify search results.")
    display_query = cleaned[:1].upper() + cleaned[1:]
    return ActionResult(True, f"Opening Spotify results for “{display_query}.”")


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


def get_trash_status(
    open_directory: OpenDirectory = os.open,
    list_directory: ListDirectory = os.listdir,
    close_directory: CloseDirectory = os.close,
) -> ActionResult:
    """Count top-level Trash entries through a fixed, no-follow directory handle."""
    trash_path = str(Path.home() / ".Trash")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = open_directory(trash_path, flags)
    except FileNotFoundError:
        return ActionResult(True, "Your Trash is empty.")
    except PermissionError:
        return ActionResult(False, "I could not inspect Trash because macOS denied permission.")
    except OSError as error:
        return ActionResult(False, f"I could not inspect Trash: {error}")

    try:
        count = len(list_directory(descriptor))
    except PermissionError:
        return ActionResult(False, "I could not inspect Trash because macOS denied permission.")
    except OSError as error:
        return ActionResult(False, f"I could not inspect Trash: {error}")
    finally:
        close_directory(descriptor)

    if count == 0:
        return ActionResult(True, "Your Trash is empty.")
    noun = "item" if count == 1 else "items"
    return ActionResult(True, f"Your Trash contains {count} {noun}.")
