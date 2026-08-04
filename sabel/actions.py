"""Validate targets and perform SABEL's approved macOS actions."""

from dataclasses import dataclass
import subprocess
from typing import Callable, Sequence, Union
from urllib.parse import urlsplit


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


def _run_open(
    arguments: Sequence[str], run_command: RunCommand
) -> Union[ActionResult, subprocess.CompletedProcess[str]]:
    """Run macOS 'open' and translate expected OS failures."""
    try:
        return run_command(
            list(arguments),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ActionResult(False, "The macOS 'open' command could not be found.")
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

    completed = _run_open(["open", url], run_command)
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

    completed = _run_open(["open", "-a", name], run_command)
    if isinstance(completed, ActionResult):
        return completed
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "application not found"
        return ActionResult(False, f"The application could not be opened: {detail}")
    return ActionResult(True, f"Opening {name}")

