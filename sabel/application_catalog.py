"""Discover and safely resolve installed macOS application bundles."""

from dataclasses import dataclass
from difflib import SequenceMatcher
import os
from pathlib import Path
import plistlib
import re
import time
from typing import Callable, Iterable, Optional
from xml.parsers.expat import ExpatError


DEFAULT_APPLICATION_ROOTS = (
    Path("/Applications"),
    Path.home() / "Applications",
    Path("/System/Applications"),
    Path("/System/Library/CoreServices"),
)

CONFIGURED_APPLICATION_ALIASES = {
    "settings": "system settings",
    "system preferences": "system settings",
}


def normalize_application_text(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    words = re.findall(r"[a-z0-9]+", value.casefold())
    normalized = ["studio" if word == "studios" else word for word in words]
    return " ".join(normalized)


def application_tokens(value: str) -> tuple[str, ...]:
    return tuple(normalize_application_text(value).split())


@dataclass(frozen=True)
class InstalledApplication:
    bundle_path: Path
    bundle_identifier: Optional[str]
    bundle_name: str
    display_name: str
    aliases: frozenset[str]


@dataclass(frozen=True)
class ApplicationCandidate:
    display_name: str
    score: float
    accepted: bool
    reason: str


@dataclass(frozen=True)
class ApplicationMatch:
    requested_name: str
    application: Optional[InstalledApplication]
    candidates: tuple[ApplicationCandidate, ...]
    message: Optional[str] = None


class ApplicationCatalog:
    """Cached catalog built only from reviewed local application roots."""

    def __init__(
        self,
        roots: Iterable[Path] = DEFAULT_APPLICATION_ROOTS,
        *,
        refresh_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.roots = tuple(Path(root).expanduser() for root in roots)
        self.refresh_seconds = max(0.0, refresh_seconds)
        self.clock = clock
        self._applications: tuple[InstalledApplication, ...] = ()
        self._refreshed_at = float("-inf")

    def applications(self, *, refresh: bool = False) -> tuple[InstalledApplication, ...]:
        now = self.clock()
        if refresh or not self._applications or now - self._refreshed_at >= self.refresh_seconds:
            self._applications = self._scan()
            self._refreshed_at = now
        return self._applications

    def refresh(self) -> tuple[InstalledApplication, ...]:
        return self.applications(refresh=True)

    def resolve(self, requested_name: str) -> ApplicationMatch:
        requested = normalize_application_text(requested_name)
        if not requested:
            return ApplicationMatch(requested_name, None, (), "Please provide an application name.")
        alias_target = CONFIGURED_APPLICATION_ALIASES.get(requested, requested)
        requested_tokens = tuple(alias_target.split())
        accepted: list[tuple[float, InstalledApplication, str]] = []
        rejected: list[ApplicationCandidate] = []

        for application in self.applications():
            if alias_target in application.aliases:
                accepted.append((1.0, application, "exact normalized name or alias"))
                continue
            best_alias = max(
                application.aliases,
                key=lambda alias: SequenceMatcher(None, alias_target, alias).ratio(),
            )
            candidate_tokens = tuple(best_alias.split())
            missing = self._missing_tokens(requested_tokens, candidate_tokens)
            if missing:
                rejected.append(
                    ApplicationCandidate(
                        application.display_name,
                        0.0,
                        False,
                        f'missing requested token "{missing[0]}"',
                    )
                )
                continue
            score = SequenceMatcher(None, alias_target, best_alias).ratio()
            if score >= 0.82:
                accepted.append((score, application, "token-complete fuzzy match"))
            else:
                rejected.append(
                    ApplicationCandidate(
                        application.display_name,
                        score,
                        False,
                        "similarity below the safe threshold",
                    )
                )

        accepted.sort(key=lambda item: (-item[0], item[1].display_name.casefold()))
        traces = [
            ApplicationCandidate(app.display_name, score, True, reason)
            for score, app, reason in accepted[:5]
        ]
        traces.extend(sorted(rejected, key=lambda item: item.display_name.casefold())[:8])
        if not accepted:
            related = self._shorter_related(requested_tokens)
            if related is not None:
                message = (
                    f"I found {related.display_name}, but I could not find {requested_name.strip()}. "
                    f"I did not open {related.display_name} because that was not the application you requested."
                )
            else:
                message = f"I could not find an installed application named {requested_name.strip()}."
            return ApplicationMatch(requested_name, None, tuple(traces), message)

        best_score, best, _ = accepted[0]
        if len(accepted) > 1 and best_score - accepted[1][0] < 0.05:
            other = accepted[1][1]
            return ApplicationMatch(
                requested_name,
                None,
                tuple(traces),
                f"I found both {best.display_name} and {other.display_name}. Which application should I open?",
            )
        return ApplicationMatch(requested_name, best, tuple(traces))

    @staticmethod
    def _missing_tokens(
        requested_tokens: tuple[str, ...], candidate_tokens: tuple[str, ...]
    ) -> list[str]:
        remaining = list(candidate_tokens)
        missing = []
        for requested in requested_tokens:
            match_index = next(
                (
                    index
                    for index, candidate in enumerate(remaining)
                    if requested == candidate
                    or SequenceMatcher(None, requested, candidate).ratio() >= 0.88
                ),
                None,
            )
            if match_index is None:
                missing.append(requested)
            else:
                remaining.pop(match_index)
        return missing

    def _shorter_related(
        self, requested_tokens: tuple[str, ...]
    ) -> Optional[InstalledApplication]:
        requested_set = set(requested_tokens)
        candidates = []
        for application in self.applications():
            for alias in application.aliases:
                tokens = set(alias.split())
                if tokens and tokens < requested_set:
                    candidates.append((len(tokens), application))
                    break
        return max(candidates, default=(0, None), key=lambda item: item[0])[1]

    def _scan(self) -> tuple[InstalledApplication, ...]:
        applications = []
        seen_paths = set()
        for root in self.roots:
            for bundle in self._bundles_under(root):
                try:
                    resolved = bundle.resolve()
                except OSError:
                    continue
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                application = self._read_bundle(bundle)
                if application is not None:
                    applications.append(application)
        return tuple(
            sorted(
                applications,
                key=lambda item: (item.display_name.casefold(), str(item.bundle_path)),
            )
        )

    @staticmethod
    def _bundles_under(root: Path, max_depth: int = 3):
        if not root.is_dir():
            return
        root_depth = len(root.parts)
        for current, directories, _ in os.walk(root):
            current_path = Path(current)
            depth = len(current_path.parts) - root_depth
            if depth >= max_depth:
                directories[:] = []
            for name in list(directories):
                if name.casefold().endswith(".app"):
                    yield current_path / name
                    directories.remove(name)

    @staticmethod
    def _read_bundle(bundle: Path) -> Optional[InstalledApplication]:
        plist_path = bundle / "Contents" / "Info.plist"
        values = {}
        try:
            with plist_path.open("rb") as stream:
                loaded = plistlib.load(stream)
                if isinstance(loaded, dict):
                    values = loaded
        except (OSError, plistlib.InvalidFileException, ExpatError):
            pass
        bundle_name = str(values.get("CFBundleName") or bundle.stem).strip()
        display_name = str(
            values.get("CFBundleDisplayName") or bundle_name or bundle.stem
        ).strip()
        identifier = values.get("CFBundleIdentifier")
        aliases = {
            normalize_application_text(bundle.stem),
            normalize_application_text(bundle_name),
            normalize_application_text(display_name),
        }
        aliases.discard("")
        if not aliases:
            return None
        return InstalledApplication(
            bundle_path=bundle,
            bundle_identifier=str(identifier) if isinstance(identifier, str) else None,
            bundle_name=bundle_name,
            display_name=display_name,
            aliases=frozenset(aliases),
        )
