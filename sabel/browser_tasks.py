"""Authoritative browser task state and bounded security audit history."""

from collections import deque
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Iterable, Optional
import uuid

from sabel.browser_models import (
    BrowserAuditEntry,
    BrowserTask,
    BrowserTaskStatus,
    ScopeExpansion,
)


DEFAULT_BROWSER_ACTIONS = {
    "browser_list_tabs",
    "browser_get_active_tab",
    "browser_open_tab",
    "browser_navigate",
    "browser_activate_tab",
    "browser_close_tab",
    "browser_get_snapshot",
    "browser_click",
    "browser_type",
    "browser_select",
    "browser_scroll",
    "browser_press_key",
    "browser_go_back",
    "browser_stop_task",
}


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("The browser audit file could not be written.")
        view = view[written:]


class BrowserTaskManager:
    def __init__(self, max_steps: int = 8, step_extension: int = 5) -> None:
        self.max_steps = max_steps
        self.step_extension = step_extension
        self.current_task: Optional[BrowserTask] = None

    def create(
        self,
        original_user_request: str,
        objective: str,
        profile_id: str,
        allowed_domains: Iterable[str],
        allowed_actions: Optional[Iterable[str]] = None,
    ) -> BrowserTask:
        task = BrowserTask(
            task_id=f"browser-task-{uuid.uuid4().hex}",
            original_user_request=original_user_request,
            objective=objective,
            profile_id=profile_id,
            allowed_domains={domain.casefold().rstrip(".") for domain in allowed_domains},
            allowed_actions=set(allowed_actions or DEFAULT_BROWSER_ACTIONS),
            allowed_profiles={profile_id},
            max_steps=self.max_steps,
        )
        self.current_task = task
        return task

    def record_step(self, task: BrowserTask) -> bool:
        if task.step_count >= task.max_steps:
            task.status = BrowserTaskStatus.STEP_LIMIT_REACHED
            return False
        task.step_count += 1
        task.status = BrowserTaskStatus.RUNNING
        return True

    def extend_steps_from_user(self, task: BrowserTask) -> None:
        task.max_steps += self.step_extension
        task.status = BrowserTaskStatus.RUNNING

    def expand_from_user(
        self,
        task: BrowserTask,
        instruction: str,
        *,
        domains: Iterable[str] = (),
        actions: Iterable[str] = (),
        profiles: Iterable[str] = (),
    ) -> ScopeExpansion:
        direct_instruction = " ".join(instruction.strip().split())[:1000]
        expansion = ScopeExpansion(
            instruction=direct_instruction,
            added_domains=frozenset(domain.casefold().rstrip(".") for domain in domains),
            added_actions=frozenset(actions),
            added_profiles=frozenset(profiles),
        )
        task.expand_from_user(expansion)
        if direct_instruction:
            task.objective = (
                f"{task.objective}\nAdditional direct user instruction: "
                f"{direct_instruction}"
            )[:2000]
        return expansion

    def stop(self, task: Optional[BrowserTask] = None) -> None:
        selected = task or self.current_task
        if selected is not None:
            selected.status = BrowserTaskStatus.CANCELLED
        if selected is self.current_task:
            self.current_task = None


class BrowserAuditLog:
    """Bounded metadata-only audit log; never stores page text or typed values."""

    def __init__(self, limit: int = 100, path: Optional[Path] = None) -> None:
        self.limit = max(1, limit)
        self.path = path
        self._entries: deque[BrowserAuditEntry] = deque(maxlen=self.limit)
        if path is not None:
            self._load()

    def record(
        self,
        *,
        task_id: str,
        profile_id: str,
        domain: str,
        action: str,
        result: str,
        confirmation_status: str,
        policy_decision: str,
        timestamp: Optional[float] = None,
    ) -> BrowserAuditEntry:
        entry = BrowserAuditEntry(
            timestamp=time.time() if timestamp is None else timestamp,
            task_id=task_id[:128],
            profile_id=profile_id[:32],
            domain=domain[:253],
            action=action[:64],
            result=result[:64],
            confirmation_status=confirmation_status[:64],
            policy_decision=policy_decision[:64],
        )
        self._entries.append(entry)
        self._write()
        return entry

    def recent(self, count: int = 10) -> list[BrowserAuditEntry]:
        return list(self._entries)[-max(0, min(count, self.limit)) :]

    def concise_summary(self, count: int = 10) -> str:
        entries = self.recent(count)
        if not entries:
            return "No browser actions have been recorded in this session."
        lines = ["Recent browser actions"]
        for entry in entries:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.timestamp))
            visible_action = entry.action.removeprefix("browser_").replace("_", " ")
            lines.append(
                f"{stamp} — {entry.profile_id} — {entry.domain or 'no domain'} — "
                f"{visible_action} — {entry.result} — {entry.policy_decision}"
            )
        return "\n".join(lines)

    def _load(self) -> None:
        assert self.path is not None
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(values, list):
            return
        for value in values[-self.limit :]:
            if not isinstance(value, dict):
                continue
            allowed = set(BrowserAuditEntry.__dataclass_fields__)
            if set(value) != allowed:
                continue
            try:
                self._entries.append(BrowserAuditEntry(**value))
            except (TypeError, ValueError):
                continue

    def _write(self) -> None:
        if self.path is None:
            return
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
                    (
                        json.dumps([asdict(entry) for entry in self._entries], indent=2)
                        + "\n"
                    ).encode("utf-8"),
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
