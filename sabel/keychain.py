"""Store and retrieve the OpenAI API key with macOS Keychain."""

import getpass
import subprocess
from typing import Callable, Optional


KEYCHAIN_SERVICE = "com.sabel.openai-api-key"
KEYCHAIN_LABEL = "SABEL OpenAI API Key"
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class KeychainError(RuntimeError):
    """Raised when a requested Keychain write cannot be completed."""


def _account_name() -> str:
    return getpass.getuser()


def read_openai_api_key(
    run_command: RunCommand = subprocess.run,
) -> Optional[str]:
    """Read SABEL's key without printing it or placing it in command arguments."""
    try:
        completed = run_command(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                _account_name(),
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    secret = completed.stdout.strip()
    return secret or None


def store_openai_api_key(
    secret: str,
    run_command: RunCommand = subprocess.run,
) -> None:
    """Add or replace the key, supplying it over stdin instead of the process list."""
    cleaned = secret.strip()
    if not cleaned.startswith("sk-") or len(cleaned) < 20:
        raise KeychainError("That does not look like an OpenAI API key.")
    try:
        completed = run_command(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-a",
                _account_name(),
                "-s",
                KEYCHAIN_SERVICE,
                "-l",
                KEYCHAIN_LABEL,
                "-U",
                "-w",
            ],
            input=cleaned + "\n",
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise KeychainError("macOS Keychain could not be reached.") from error
    if completed.returncode != 0:
        raise KeychainError("macOS Keychain did not save the API key.")
