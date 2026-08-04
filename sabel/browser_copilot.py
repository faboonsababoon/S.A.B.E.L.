"""Profile-aware high-level browser operations with observe–act–verify tasks."""

import asyncio
from dataclasses import dataclass
import re
from typing import Callable, Optional, Protocol
from urllib.parse import quote_plus
import uuid

from sabel.browser_models import (
    BrowserActionState,
    BrowserProfile,
    BrowserResult,
    BrowserTask,
    BrowserTaskStatus,
)
from sabel.browser_policy import (
    BrowserActionProposal,
    BrowserPolicy,
    PolicyDecisionType,
    browser_planner_context,
)
from sabel.browser_security import (
    BrowserSecurityError,
    domain_in_scope,
    normalized_domain,
)
from sabel.browser_tasks import BrowserAuditLog, BrowserTaskManager
from sabel.browser_transport import BrowserTransport
from sabel.services import Service, resolve_profile_service


@dataclass(frozen=True)
class BrowserOutcome:
    state: BrowserActionState
    message: str
    success: bool
    verified: bool = False
    task_id: Optional[str] = None
    confirmation_prompt: Optional[str] = None
    clarification: Optional[str] = None


class BrowserPlanner(Protocol):
    def next_action(
        self, task: BrowserTask, snapshot: dict[str, object]
    ) -> Optional[BrowserActionProposal]: ...


class YouTubeChannelPlanner:
    """Select one channel link from an untrusted snapshot without executing it."""

    def __init__(self, target: str) -> None:
        self.target = target

    def next_action(
        self, task: BrowserTask, snapshot: dict[str, object]
    ) -> Optional[BrowserActionProposal]:
        # Build the separated context even though this conservative v1 planner uses
        # deterministic matching. An Ollama planner can consume the same boundary.
        browser_planner_context(task, snapshot, ["browser_click", "browser_scroll"])
        target_tokens = set(re.findall(r"[a-z0-9]+", self.target.casefold()))
        candidates = []
        for element in snapshot.get("interactive_elements", []):
            if not isinstance(element, dict):
                continue
            href = str(element.get("href") or "")
            text = " ".join(
                str(element.get(key) or "")
                for key in ("visible_text", "accessible_name")
            ).casefold()
            text_tokens = set(re.findall(r"[a-z0-9]+", text))
            is_channel = any(marker in href for marker in ("/channel/", "/@", "/c/"))
            if is_channel and target_tokens and target_tokens.issubset(text_tokens):
                candidates.append(element)
        if not candidates:
            return None
        selected = candidates[0]
        return BrowserActionProposal(
            action="browser_click",
            arguments={
                "tab_id": int(snapshot["tab_id"]),
                "snapshot_id": str(snapshot["snapshot_id"]),
                "element_id": str(selected["element_id"]),
            },
            reason=f"open the channel result matching {self.target}",
            expected_result=f"a YouTube channel page for {self.target}",
        )


class BrowserCopilot:
    def __init__(
        self,
        transport: BrowserTransport,
        *,
        task_manager: Optional[BrowserTaskManager] = None,
        policy: Optional[BrowserPolicy] = None,
        audit_log: Optional[BrowserAuditLog] = None,
        albert_url: Optional[str] = None,
        default_gmail_profile: Optional[str] = None,
    ) -> None:
        self.transport = transport
        self.task_manager = task_manager or BrowserTaskManager()
        self.policy = policy or BrowserPolicy()
        self.audit_log = audit_log or BrowserAuditLog()
        self.albert_url = albert_url
        self.default_gmail_profile = default_gmail_profile

    def connected_profiles(self) -> list[BrowserProfile]:
        return self.transport.connected_profiles()

    def show_profiles(self) -> BrowserOutcome:
        profiles = self.connected_profiles()
        if not profiles:
            return BrowserOutcome(
                BrowserActionState.EXECUTED,
                "No SABEL browser profiles are currently connected.",
                True,
            )
        lines = ["Connected browser profiles"]
        lines.extend(f"{profile.profile_name} ({profile.profile_id})" for profile in profiles)
        return BrowserOutcome(BrowserActionState.VERIFIED, "\n".join(lines), True, True)

    async def show_tabs(self, profile_id: str) -> BrowserOutcome:
        disconnected = self._disconnected_profile(profile_id)
        if disconnected:
            return disconnected
        result = await self.transport.send_request(
            profile_id, "browser_list_tabs", {}
        )
        self._audit_direct(
            profile_id,
            "",
            "browser_list_tabs",
            result.state.value,
            "read_only_allow",
        )
        if not result.success:
            return self._transport_failure(result)
        tabs = result.result.get("tabs")
        if not isinstance(tabs, list) or not tabs:
            return BrowserOutcome(
                BrowserActionState.VERIFIED,
                f"No tabs are open in the {self._profile_name(profile_id)} Chrome profile.",
                True,
                True,
            )
        lines = [f"Tabs in {self._profile_name(profile_id)}"]
        for tab in tabs[:50]:
            if not isinstance(tab, dict):
                continue
            title = " ".join(str(tab.get("title") or "Untitled").split())[:120]
            try:
                domain = normalized_domain(tab.get("url"))
            except BrowserSecurityError:
                domain = "restricted page"
            active = " (active)" if tab.get("active") is True else ""
            lines.append(f"{title} — {domain}{active}")
        return BrowserOutcome(
            BrowserActionState.VERIFIED, "\n".join(lines), True, True
        )

    async def open_service(
        self,
        service_name: str,
        profile_id: Optional[str] = None,
    ) -> BrowserOutcome:
        resolution = resolve_profile_service(
            service_name,
            albert_url=self.albert_url,
            default_gmail_profile=profile_id or self.default_gmail_profile,
        )
        if resolution.clarification:
            return BrowserOutcome(
                BrowserActionState.PLANNED,
                resolution.clarification,
                False,
                clarification=resolution.clarification,
            )
        service = resolution.service
        if service is None:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "I do not recognize that browser service.",
                False,
            )
        if not service.url:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "Albert is not configured yet. Set SABEL_ALBERT_URL to its exact HTTPS address.",
                False,
            )
        selected_profile = profile_id or service.default_profile
        if not selected_profile:
            return BrowserOutcome(
                BrowserActionState.PLANNED,
                "Which Chrome profile should I use: Personal or NYU?",
                False,
                clarification="Which Chrome profile should I use: Personal or NYU?",
            )
        if (
            resolution.service_name in {"albert", "nyu_gmail", "personal_gmail"}
            and selected_profile != service.default_profile
        ):
            return BrowserOutcome(
                BrowserActionState.FAILED,
                f"{service.display_name} is assigned to the {service.default_profile.upper() if service.default_profile == 'nyu' else 'Personal'} browser profile; SABEL will not substitute another account profile.",
                False,
            )
        disconnected = self._disconnected_profile(selected_profile)
        if disconnected:
            return disconnected
        result = await self.transport.send_request(
            selected_profile,
            "browser_open_tab",
            {"url": service.url, "active": True},
        )
        self._audit_direct(
            selected_profile,
            normalized_domain(service.url),
            "browser_open_tab",
            result.state.value,
            "service_registry_allow",
        )
        if not result.success:
            return self._transport_failure(result)
        profile_name = self._profile_name(selected_profile)
        return BrowserOutcome(
            BrowserActionState.EXECUTED,
            f"Opening {service.display_name} in your {profile_name} Chrome profile.",
            True,
            False,
        )

    async def browser_search(
        self,
        service_name: str,
        query: str,
        profile_id: Optional[str] = None,
    ) -> BrowserOutcome:
        resolution = resolve_profile_service(service_name)
        service = resolution.service
        if service is None or resolution.service_name not in {"youtube", "google"}:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "Browser search currently supports YouTube and Google.",
                False,
            )
        cleaned = " ".join(query.strip().split())
        if not cleaned:
            return BrowserOutcome(BrowserActionState.FAILED, "Please provide a search query.", False)
        selected_profile = profile_id or service.default_profile or "personal"
        disconnected = self._disconnected_profile(selected_profile)
        if disconnected:
            return disconnected
        if resolution.service_name == "youtube":
            url = "https://www.youtube.com/results?search_query=" + quote_plus(cleaned)
        else:
            url = "https://www.google.com/search?q=" + quote_plus(cleaned)
        result = await self.transport.send_request(
            selected_profile, "browser_open_tab", {"url": url, "active": True}
        )
        self._audit_direct(
            selected_profile,
            normalized_domain(url),
            "browser_open_tab",
            result.state.value,
            "search_registry_allow",
        )
        if not result.success:
            return self._transport_failure(result)
        return BrowserOutcome(
            BrowserActionState.EXECUTED,
            f"Searching {service.display_name} for “{cleaned}.”",
            True,
        )

    async def open_youtube_channel(
        self,
        target: str,
        original_request: str,
        approval_callback: Optional[Callable[[str], str]] = None,
    ) -> BrowserOutcome:
        cleaned_target = " ".join(target.strip().split())
        if not cleaned_target:
            return BrowserOutcome(BrowserActionState.FAILED, "Please name a YouTube channel.", False)
        disconnected = self._disconnected_profile("personal")
        if disconnected:
            return disconnected
        task = self.task_manager.create(
            original_request,
            f"Open {cleaned_target}'s YouTube channel",
            "personal",
            {"youtube.com"},
        )
        search_url = "https://www.youtube.com/results?search_query=" + quote_plus(cleaned_target)
        opened = await self._execute(
            task,
            BrowserActionProposal(
                "browser_open_tab",
                {"url": search_url, "active": True},
                f"open YouTube search results for {cleaned_target}",
                "YouTube search results open",
            ),
            approval_callback=approval_callback,
        )
        if isinstance(opened, BrowserOutcome):
            return opened
        task.current_tab_id = self._tab_id(opened)
        if task.current_tab_id is None:
            task.status = BrowserTaskStatus.FAILED
            return self._failed_search(cleaned_target, task)

        page = await self._snapshot(task)
        if isinstance(page, BrowserOutcome):
            return page
        planner = YouTubeChannelPlanner(cleaned_target)
        proposal = planner.next_action(task, page)
        if proposal is None:
            task.status = BrowserTaskStatus.FAILED
            return self._failed_search(cleaned_target, task)
        clicked = await self._execute(
            task,
            proposal,
            snapshot=page,
            approval_callback=approval_callback,
        )
        if isinstance(clicked, BrowserOutcome):
            return clicked

        # A content-script click returns before Chrome necessarily commits the
        # navigation. Yield briefly so the following URL and snapshot observation
        # describes the destination rather than the stale search document.
        await asyncio.sleep(0.25)

        active = await self._execute(
            task,
            BrowserActionProposal(
                "browser_get_active_tab",
                {},
                "observe the tab after clicking the channel result",
                "the active tab reports its new URL",
            ),
            approval_callback=approval_callback,
        )
        if isinstance(active, BrowserOutcome):
            return active
        task.current_tab_id = self._tab_id(active) or task.current_tab_id
        final_page = await self._snapshot(task)
        if isinstance(final_page, BrowserOutcome):
            return final_page
        if self._verify_youtube_channel(cleaned_target, final_page):
            task.status = BrowserTaskStatus.VERIFIED
            return BrowserOutcome(
                BrowserActionState.VERIFIED,
                f"Opened {cleaned_target}’s YouTube channel.",
                True,
                True,
                task.task_id,
            )
        task.status = BrowserTaskStatus.FAILED
        return self._failed_search(cleaned_target, task)

    async def stop_task(self) -> BrowserOutcome:
        task = self.task_manager.current_task
        profile_ids = (
            [task.profile_id]
            if task is not None
            else [profile.profile_id for profile in self.connected_profiles()]
        )
        if not profile_ids:
            return BrowserOutcome(
                BrowserActionState.CANCELLED,
                "There is no connected browser profile to stop.",
                True,
            )
        for profile_id in profile_ids:
            arguments = {"task_id": task.task_id} if task is not None else {}
            result = await self.transport.send_request(
                profile_id, "browser_stop_task", arguments
            )
            self._audit_direct(
                profile_id,
                "",
                "browser_stop_task",
                result.state.value,
                "emergency_stop_allow",
            )
            if not result.success and result.error_code != "CANCELLED":
                return self._transport_failure(result, task.task_id if task else None)
        if task is not None:
            self.task_manager.stop(task)
        return BrowserOutcome(
            BrowserActionState.CANCELLED,
            "Browser control stopped. Manual browsing was not affected.",
            True,
            task_id=task.task_id if task else None,
        )

    async def _snapshot(self, task: BrowserTask):
        if task.current_tab_id is None:
            return BrowserOutcome(BrowserActionState.FAILED, "The browser task has no active tab.", False, task_id=task.task_id)
        result = await self._execute(
            task,
            BrowserActionProposal(
                "browser_get_snapshot",
                {"tab_id": task.current_tab_id},
                "observe the current page",
                "a constrained page snapshot is returned",
            ),
        )
        if isinstance(result, BrowserOutcome):
            return result
        snapshot = result.result
        snapshot_id = snapshot.get("snapshot_id")
        tab_id = snapshot.get("tab_id")
        url = snapshot.get("url")
        elements = snapshot.get("interactive_elements")
        if (
            not isinstance(snapshot_id, str)
            or not snapshot_id
            or not isinstance(tab_id, int)
            or isinstance(tab_id, bool)
            or tab_id != task.current_tab_id
            or not isinstance(elements, list)
            or len(elements) > 120
            or not isinstance(snapshot.get("title"), str)
            or len(snapshot["title"]) > 300
            or not isinstance(snapshot.get("visible_text_summary"), str)
            or len(snapshot["visible_text_summary"]) > 6000
        ):
            task.status = BrowserTaskStatus.FAILED
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser returned a malformed page snapshot.",
                False,
                task_id=task.task_id,
            )
        try:
            current_domain = normalized_domain(url)
        except BrowserSecurityError:
            current_domain = ""
        if not current_domain or not domain_in_scope(
            current_domain, task.allowed_domains
        ):
            task.status = BrowserTaskStatus.FAILED
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser unexpectedly left the user-authorized domains, so I stopped the task.",
                False,
                task_id=task.task_id,
            )
        task.last_snapshot_id = snapshot_id
        task.last_url = str(url)
        return result.result

    async def _execute(
        self,
        task: BrowserTask,
        proposal: BrowserActionProposal,
        *,
        snapshot: Optional[dict[str, object]] = None,
        approval_callback: Optional[Callable[[str], str]] = None,
    ):
        decision = self.policy.evaluate(task, proposal, snapshot)
        confirmed = False
        if decision.decision == PolicyDecisionType.REQUIRE_CONFIRMATION:
            task.status = BrowserTaskStatus.WAITING_FOR_CONFIRMATION
            answer = approval_callback(decision.confirmation_prompt or "Continue? [y/N] ") if approval_callback else ""
            if answer.strip().casefold() not in {"y", "yes"}:
                self._audit(task, proposal, "cancelled", "declined", decision.decision.value, snapshot)
                return BrowserOutcome(
                    BrowserActionState.CANCELLED,
                    "Browser action cancelled. No page action was performed.",
                    False,
                    task_id=task.task_id,
                )
            confirmed = True
            decision = self.policy.evaluate(task, proposal, snapshot, confirmed=True)
        if decision.decision != PolicyDecisionType.ALLOW:
            self._audit(task, proposal, "rejected", "not_confirmed", decision.decision.value, snapshot)
            if decision.code == "STEP_LIMIT_REACHED":
                task.status = BrowserTaskStatus.STEP_LIMIT_REACHED
                message = (
                    f"I have not verified the requested result after {task.max_steps} browser actions. "
                    f"Continue for another {self.task_manager.step_extension} actions or stop?"
                )
            else:
                task.status = BrowserTaskStatus.FAILED
                message = decision.reason
            return BrowserOutcome(
                BrowserActionState.FAILED,
                message,
                False,
                task_id=task.task_id,
            )
        if not self.task_manager.record_step(task):
            return BrowserOutcome(
                BrowserActionState.FAILED,
                f"I have not verified the requested result after {task.max_steps} browser actions. Continue for another {self.task_manager.step_extension} actions or stop?",
                False,
                task_id=task.task_id,
            )
        result = await self.transport.send_request(
            task.profile_id, proposal.action, proposal.arguments
        )
        self._audit(
            task,
            proposal,
            result.state.value,
            "confirmed" if confirmed else "not_required",
            "allow",
            snapshot,
        )
        if not result.success:
            task.status = BrowserTaskStatus.FAILED
            return self._transport_failure(result, task.task_id)
        return result

    def _audit(
        self,
        task: BrowserTask,
        proposal: BrowserActionProposal,
        result: str,
        confirmation: str,
        policy: str,
        snapshot: Optional[dict[str, object]],
    ) -> None:
        domain = ""
        url = proposal.arguments.get("url") or (snapshot or {}).get("url")
        if url:
            try:
                domain = normalized_domain(url)
            except BrowserSecurityError:
                domain = "invalid"
        self.audit_log.record(
            task_id=task.task_id,
            profile_id=task.profile_id,
            domain=domain,
            action=proposal.action,
            result=result,
            confirmation_status=confirmation,
            policy_decision=policy,
        )

    def _audit_direct(
        self,
        profile_id: str,
        domain: str,
        action: str,
        result: str,
        policy: str,
    ) -> None:
        self.audit_log.record(
            task_id=f"browser-direct-{uuid.uuid4().hex}",
            profile_id=profile_id,
            domain=domain,
            action=action,
            result=result,
            confirmation_status="not_required",
            policy_decision=policy,
        )

    def _disconnected_profile(self, profile_id: str) -> Optional[BrowserOutcome]:
        if any(profile.profile_id == profile_id for profile in self.connected_profiles()):
            return None
        label = "NYU" if profile_id == "nyu" else "Personal"
        return BrowserOutcome(
            BrowserActionState.FAILED,
            f"The {label} browser profile is not currently connected.",
            False,
        )

    def _profile_name(self, profile_id: str) -> str:
        for profile in self.connected_profiles():
            if profile.profile_id == profile_id:
                return profile.profile_name
        return "NYU" if profile_id == "nyu" else "Personal"

    @staticmethod
    def _tab_id(result: BrowserResult) -> Optional[int]:
        value = result.result.get("tab_id")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _verify_youtube_channel(target: str, snapshot: dict[str, object]) -> bool:
        try:
            if normalized_domain(snapshot.get("url")) != "www.youtube.com":
                return False
        except BrowserSecurityError:
            return False
        url = str(snapshot.get("url") or "")
        if not any(marker in url for marker in ("/channel/", "/@", "/c/")):
            return False
        evidence = (
            str(snapshot.get("title") or "")
            + " "
            + str(snapshot.get("visible_text_summary") or "")[:2000]
        ).casefold()
        tokens = set(re.findall(r"[a-z0-9]+", target.casefold()))
        return bool(tokens and tokens.issubset(set(re.findall(r"[a-z0-9]+", evidence))))

    @staticmethod
    def _failed_search(target: str, task: BrowserTask) -> BrowserOutcome:
        return BrowserOutcome(
            BrowserActionState.FAILED,
            f"I opened YouTube search results for {target}, but I could not verify the official channel result.",
            False,
            False,
            task.task_id,
        )

    @staticmethod
    def _transport_failure(
        result: BrowserResult, task_id: Optional[str] = None
    ) -> BrowserOutcome:
        return BrowserOutcome(
            result.state,
            result.error or "The browser action failed.",
            False,
            False,
            task_id,
        )
