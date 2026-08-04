"""Token, extension-origin, URL, and local rate-limit security controls."""

from collections import deque
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
from typing import Callable, Optional
from urllib.parse import urlsplit


EXTENSION_ID_PATTERN = re.compile(r"^[a-p]{32}$")
PROFILE_IDS = {"personal", "nyu"}


class BrowserSecurityError(RuntimeError):
    pass


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("The secure configuration file could not be written.")
        view = view[written:]


class BrowserTokenStore:
    def __init__(
        self,
        path: Path,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        self.path = path
        self.token_factory = token_factory

    def _prepare_directory(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass

    def load_or_create(self) -> str:
        self._prepare_directory()
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return self._create()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BrowserSecurityError("The browser token path is not a regular file.")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags)
        try:
            token = os.read(descriptor, 512).decode("utf-8").strip()
        finally:
            os.close(descriptor)
        if not token or len(token) > 256:
            raise BrowserSecurityError("The stored browser token is invalid.")
        return token

    def _create(self) -> str:
        token = self.token_factory(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        try:
            _write_all(descriptor, (token + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return token

    def rotate(self) -> str:
        self._prepare_directory()
        token = self.token_factory(32)
        temporary = self.path.with_name(self.path.name + ".new")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags, 0o600)
        except FileExistsError:
            temporary.unlink()
            descriptor = os.open(temporary, flags, 0o600)
        try:
            _write_all(descriptor, (token + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        return token

    @staticmethod
    def matches(expected: str, supplied: object) -> bool:
        return isinstance(supplied, str) and secrets.compare_digest(expected, supplied)


class ExtensionOriginStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def register_extension_id(self, extension_id: str) -> str:
        normalized = extension_id.strip().casefold()
        if not EXTENSION_ID_PATTERN.fullmatch(normalized):
            raise BrowserSecurityError("Chrome extension IDs must contain 32 letters from a through p.")
        origins = self.allowed_origins()
        origin = f"chrome-extension://{normalized}"
        origins.add(origin)
        self._write(origins)
        return origin

    def allowed_origins(self) -> set[str]:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return set()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise BrowserSecurityError(
                "The browser-origin allowlist path is not a regular file."
            )
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as error:
            raise BrowserSecurityError("The browser-origin allowlist could not be read.") from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BrowserSecurityError("The browser-origin allowlist is malformed.") from error
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise BrowserSecurityError("The browser-origin allowlist is malformed.")
        valid = {
            item
            for item in value
            if item.startswith("chrome-extension://")
            and EXTENSION_ID_PATTERN.fullmatch(item.removeprefix("chrome-extension://"))
        }
        if len(valid) != len(value):
            raise BrowserSecurityError("The browser-origin allowlist contains an invalid origin.")
        return valid

    def is_allowed(self, origin: Optional[str]) -> bool:
        return isinstance(origin, str) and origin in self.allowed_origins()

    def _write(self, origins: set[str]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=self.path.name + ".",
                suffix=".new",
                dir=self.path.parent,
            )
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                _write_all(
                    descriptor,
                    (json.dumps(sorted(origins), indent=2) + "\n").encode("utf-8"),
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, self.path)
        finally:
            if "temporary" in locals():
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.events: deque[float] = deque()

    def allow(self, now: Optional[float] = None) -> bool:
        current = time.monotonic() if now is None else now
        while self.events and current - self.events[0] >= self.window_seconds:
            self.events.popleft()
        if len(self.events) >= self.limit:
            return False
        self.events.append(current)
        return True


def validate_http_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise BrowserSecurityError("A valid HTTP or HTTPS URL is required.")
    candidate = value.strip()
    if any(character.isspace() or ord(character) < 32 for character in candidate):
        raise BrowserSecurityError("That browser URL is malformed.")
    parsed = urlsplit(candidate)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise BrowserSecurityError("Browser navigation allows only HTTP and HTTPS URLs.")
    if parsed.username or parsed.password:
        raise BrowserSecurityError("Credentials are not permitted in browser URLs.")
    return candidate


def normalized_domain(url: object) -> str:
    return (urlsplit(validate_http_url(url)).hostname or "").casefold().rstrip(".")


def domain_in_scope(domain: str, allowed_domains: set[str]) -> bool:
    normalized = domain.casefold().rstrip(".")
    return any(
        normalized == allowed.casefold().rstrip(".")
        or normalized.endswith("." + allowed.casefold().rstrip("."))
        for allowed in allowed_domains
    )
