"""Profile-aware high-level browser operations with observe–act–verify tasks."""

import asyncio
from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import Callable, Optional, Protocol
from urllib.parse import quote_plus
import uuid

from sabel.browser_models import (
    BrowserActionContext,
    BrowserActionState,
    BrowserDecision,
    BrowserDecisionAction,
    BrowserProfile,
    BrowserResult,
    BrowserSearchRequest,
    BrowserTask,
    BrowserTaskStatus,
    PendingBrowserAction,
    ProfileBrowserState,
)
from sabel.browser_planner import BrowserPlannerError
from sabel.browser_routing import (
    SEARCH_PROVIDER_URLS,
    WEB_SEARCH_ENGINES,
    build_search_url,
    normalize_search_engine,
    search_provider_display_name,
    validate_search_query,
    verify_search_url,
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
    restricted_domain,
    validate_http_url,
)
from sabel.browser_tasks import BrowserAuditLog, BrowserTaskManager
from sabel.browser_transport import BrowserTransport
from sabel.services import Service, resolve_profile_service
from sabel.request_models import BrowserExecutionEvidence


@dataclass(frozen=True)
class BrowserOutcome:
    state: BrowserActionState
    message: str
    success: bool
    verified: bool = False
    task_id: Optional[str] = None
    confirmation_prompt: Optional[str] = None
    clarification: Optional[str] = None
    context: Optional[BrowserActionContext] = None
    target_tab_id: Optional[int] = None
    evidence: Optional[BrowserExecutionEvidence] = None


class BrowserPlanner(Protocol):
    def next_action(
        self, task: BrowserTask, snapshot: dict[str, object]
    ) -> BrowserDecision: ...


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
        planner: Optional[BrowserPlanner] = None,
    ) -> None:
        self.transport = transport
        self.task_manager = task_manager or BrowserTaskManager()
        self.policy = policy or BrowserPolicy()
        self.audit_log = audit_log or BrowserAuditLog()
        self.albert_url = albert_url
        self.default_gmail_profile = default_gmail_profile
        self.planner = planner
        self.browser_state_by_profile: dict[str, ProfileBrowserState] = {}

    def connected_profiles(self) -> list[BrowserProfile]:
        return self.transport.connected_profiles()

    def show_profiles(self) -> BrowserOutcome:
        order = {"personal": 0, "nyu": 1}
        profiles = sorted(
            self.connected_profiles(),
            key=lambda profile: (order.get(profile.profile_id, 99), profile.profile_id),
        )
        if not profiles:
            return BrowserOutcome(
                BrowserActionState.EXECUTED,
                "No SABEL browser profiles are currently connected.",
                True,
            )
        lines = ["Connected browser profiles"]
        lines.extend(
            f"- {profile.profile_name} ({profile.profile_id})" for profile in profiles
        )
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
        if result.profile_id != profile_id:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser returned tabs from the wrong Chrome profile.",
                False,
            )
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
            return replace(
                disconnected,
                evidence=BrowserExecutionEvidence(
                    requested_profile_id=selected_profile,
                    requested_url=service.url,
                    verified=False,
                    error_code="PROFILE_DISCONNECTED",
                ),
            )
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
            return self._transport_failure(
                result,
                requested_profile_id=selected_profile,
                requested_url=service.url,
            )
        verified = self._verified_tab_result(result, selected_profile, service.url)
        if isinstance(verified, BrowserOutcome):
            return verified
        tab_id, resulting_url = verified
        search_engine = (
            resolution.service_name
            if resolution.service_name in SEARCH_PROVIDER_URLS
            else None
        )
        context = BrowserActionContext(
            profile_id=selected_profile,
            service=resolution.service_name,
            search_engine=search_engine,
            tab_id=tab_id,
            url=resulting_url,
            connection_instance_id=result.connection_instance_id,
        )
        self._update_profile_state(context, "open_service")
        profile_name = self._profile_name(selected_profile)
        return BrowserOutcome(
            BrowserActionState.VERIFIED,
            f"Opening {service.display_name} in your {profile_name} Chrome profile.",
            True,
            True,
            context=context,
            evidence=BrowserExecutionEvidence(
                requested_profile_id=selected_profile,
                requested_url=service.url,
                request_id=result.request_id,
                result_profile_id=result.profile_id,
                result_url=resulting_url,
                result_tab_id=tab_id,
                verified=True,
            ),
        )

    async def search_web(
        self,
        query: str,
        search_engine: str,
        profile_id: Optional[str] = None,
    ) -> BrowserOutcome:
        selected_engine = normalize_search_engine(search_engine)
        if selected_engine not in WEB_SEARCH_ENGINES:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "Browser web search supports Google, Bing, and DuckDuckGo.",
                False,
            )
        return await self._search(query, selected_engine, profile_id or "personal")

    async def search_youtube(
        self,
        query: str,
        profile_id: Optional[str] = None,
    ) -> BrowserOutcome:
        return await self._search(query, "youtube", profile_id or "personal")

    async def browser_search(
        self,
        service_name: str,
        query: str,
        profile_id: Optional[str] = None,
    ) -> BrowserOutcome:
        """Compatibility adapter; new router results use distinct search tools."""
        selected = normalize_search_engine(service_name)
        if selected == "youtube":
            return await self.search_youtube(query, profile_id)
        if selected in WEB_SEARCH_ENGINES:
            return await self.search_web(query, selected, profile_id)
        return BrowserOutcome(
            BrowserActionState.FAILED,
            "Browser search supports YouTube, Google, Bing, and DuckDuckGo.",
            False,
        )

    async def run_browser_task(
        self,
        objective: str,
        profile_id: str,
        *,
        service_name: Optional[str] = None,
        initial_url: Optional[str] = None,
    ) -> BrowserOutcome:
        """Run a bounded observe–plan–validate–act loop in one exact profile."""
        goal = " ".join(objective.strip().split())
        if not goal or len(goal) > 1000:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "Please provide a browser task under 1,000 characters.",
                False,
            )
        selected_profile = profile_id.strip().casefold()
        if selected_profile not in {"personal", "nyu"}:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser profile must be Personal or NYU.",
                False,
            )
        if self.planner is None:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The local browser planner is unavailable.",
                False,
            )
        if bool(service_name) == bool(initial_url):
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "A browser task requires exactly one reviewed service or explicit HTTP/HTTPS URL.",
                False,
            )

        service_key = None
        display_name = "the requested site"
        if service_name:
            resolution = resolve_profile_service(
                service_name,
                albert_url=self.albert_url,
                default_gmail_profile=selected_profile,
            )
            service = resolution.service
            if resolution.clarification:
                return BrowserOutcome(
                    BrowserActionState.PLANNED,
                    resolution.clarification,
                    False,
                    clarification=resolution.clarification,
                )
            if service is None:
                return BrowserOutcome(
                    BrowserActionState.FAILED,
                    "I do not recognize that browser service. Provide an explicit HTTP or HTTPS URL instead.",
                    False,
                )
            if not service.url:
                return BrowserOutcome(
                    BrowserActionState.FAILED,
                    "Albert is not configured yet. Set SABEL_ALBERT_URL to its exact HTTPS address.",
                    False,
                )
            if (
                resolution.service_name in {"albert", "nyu_gmail", "personal_gmail"}
                and selected_profile != service.default_profile
            ):
                required = "NYU" if service.default_profile == "nyu" else "Personal"
                return BrowserOutcome(
                    BrowserActionState.FAILED,
                    f"{service.display_name} is assigned to the {required} browser profile; SABEL will not substitute another profile.",
                    False,
                )
            selected_url = service.url
            service_key = resolution.service_name
            display_name = service.display_name
            allowed_domains = set(service.allowed_domains)
        else:
            try:
                selected_url = validate_http_url(initial_url)
            except BrowserSecurityError as error:
                return BrowserOutcome(BrowserActionState.FAILED, str(error), False)
            allowed_domains = {normalized_domain(selected_url)}

        try:
            initial_domain = normalized_domain(selected_url)
        except BrowserSecurityError as error:
            return BrowserOutcome(BrowserActionState.FAILED, str(error), False)
        if restricted_domain(initial_domain):
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "That domain is outside SABEL's permitted browser scope.",
                False,
            )
        allowed_domains.add(initial_domain)
        disconnected = self._disconnected_profile(selected_profile)
        if disconnected:
            return disconnected

        task = self.task_manager.create(
            objective,
            goal,
            selected_profile,
            allowed_domains,
            initial_url=selected_url,
            initial_service=service_key,
        )
        opened = await self._execute(
            task,
            BrowserActionProposal(
                "browser_open_tab",
                {"url": selected_url, "active": True},
                f"open {display_name}",
                f"{display_name} opens in the requested profile",
            ),
            count_step=False,
        )
        if isinstance(opened, BrowserOutcome):
            return opened
        task.current_tab_id = self._tab_id(opened)
        if task.current_tab_id is None:
            task.status = BrowserTaskStatus.FAILED
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser did not return a usable tab for that task.",
                False,
                task_id=task.task_id,
            )
        return await self._run_agent_loop(task)

    async def resume_browser_task(self, task_id: str) -> BrowserOutcome:
        """Execute only the exact Python-stored action after central confirmation."""
        task = next(
            (
                item
                for item in self.task_manager.current_tasks_by_profile.values()
                if item.task_id == task_id
            ),
            None,
        )
        if task is None or task.pending_action is None:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "That pending browser action is no longer available.",
                False,
            )
        pending = task.pending_action
        proposal = self._proposal_from_decision(task, pending.decision, pending.snapshot)
        task.pending_action = None
        result = await self._execute(
            task,
            proposal,
            snapshot=pending.snapshot,
            confirmed=True,
        )
        if isinstance(result, BrowserOutcome):
            return result
        self._record_agent_action(task, pending.decision, pending.snapshot)
        self._update_task_tab(task, result)
        return await self._run_agent_loop(task)

    def cancel_pending_browser_task(self, task_id: str) -> BrowserOutcome:
        task = next(
            (
                item
                for item in self.task_manager.current_tasks_by_profile.values()
                if item.task_id == task_id
            ),
            None,
        )
        if task is None:
            return BrowserOutcome(
                BrowserActionState.CANCELLED,
                "That browser task is no longer active.",
                True,
            )
        task.pending_action = None
        self.task_manager.stop(task)
        return BrowserOutcome(
            BrowserActionState.CANCELLED,
            "Browser action cancelled. No page action was performed.",
            True,
            task_id=task_id,
        )

    async def _run_agent_loop(self, task: BrowserTask) -> BrowserOutcome:
        while task.step_count < task.max_steps:
            page = await self._snapshot(task)
            if isinstance(page, BrowserOutcome):
                return page
            fingerprint = self._snapshot_fingerprint(page)
            try:
                decision = self.planner.next_action(task, page) if self.planner else None
            except BrowserPlannerError as error:
                task.status = BrowserTaskStatus.FAILED
                return BrowserOutcome(
                    BrowserActionState.FAILED,
                    f"I could not safely parse the local browser planner's decision: {error}",
                    False,
                    task_id=task.task_id,
                )
            except Exception:
                task.status = BrowserTaskStatus.FAILED
                return BrowserOutcome(
                    BrowserActionState.FAILED,
                    "The local browser planner failed before any new page action was performed.",
                    False,
                    task_id=task.task_id,
                )
            if not isinstance(decision, BrowserDecision):
                task.status = BrowserTaskStatus.FAILED
                return BrowserOutcome(
                    BrowserActionState.FAILED,
                    "The local browser planner returned a malformed decision.",
                    False,
                    task_id=task.task_id,
                )
            if decision.action == BrowserDecisionAction.DONE:
                if not self._completion_verified(decision, page):
                    task.status = BrowserTaskStatus.FAILED
                    return BrowserOutcome(
                        BrowserActionState.FAILED,
                        "The browser planner claimed completion without enough current page evidence, so I did not report success.",
                        False,
                        task_id=task.task_id,
                    )
                task.status = BrowserTaskStatus.VERIFIED
                context = BrowserActionContext(
                    profile_id=task.profile_id,
                    service=task.initial_service,
                    tab_id=task.current_tab_id,
                    url=str(page.get("url") or ""),
                )
                self._update_profile_state(context, "browser_task")
                return BrowserOutcome(
                    BrowserActionState.VERIFIED,
                    decision.answer or "Browser task completed.",
                    True,
                    True,
                    task.task_id,
                    context=context,
                )
            if decision.action == BrowserDecisionAction.CLARIFY:
                task.status = BrowserTaskStatus.CLARIFICATION_REQUIRED
                return BrowserOutcome(
                    BrowserActionState.PLANNED,
                    decision.question or "I need more information to continue that browser task.",
                    False,
                    task_id=task.task_id,
                    clarification=decision.question,
                )

            signature = self._decision_signature(decision, page)
            if (
                task.last_action_signature == signature
                and task.last_snapshot_fingerprint == fingerprint
            ):
                task.no_progress_count += 1
            else:
                task.no_progress_count = 0
            if task.no_progress_count >= task.max_no_progress:
                task.status = BrowserTaskStatus.FAILED
                return BrowserOutcome(
                    BrowserActionState.FAILED,
                    "I stopped because the browser task repeated the same action without page progress.",
                    False,
                    task_id=task.task_id,
                )

            try:
                proposal = self._proposal_from_decision(task, decision, page)
            except BrowserPlannerError as error:
                task.status = BrowserTaskStatus.FAILED
                return BrowserOutcome(
                    BrowserActionState.FAILED,
                    f"I could not safely execute the browser planner's decision: {error}",
                    False,
                    task_id=task.task_id,
                )
            result = await self._execute(task, proposal, snapshot=page)
            if isinstance(result, BrowserOutcome):
                if result.confirmation_prompt:
                    task.pending_action = PendingBrowserAction(
                        decision=decision,
                        snapshot=page,
                        prompt=result.confirmation_prompt,
                    )
                return result
            self._record_agent_action(task, decision, page)
            self._update_task_tab(task, result)
            if decision.action in {
                BrowserDecisionAction.CLICK,
                BrowserDecisionAction.NAVIGATE,
                BrowserDecisionAction.OPEN_TAB,
                BrowserDecisionAction.BACK,
                BrowserDecisionAction.PRESS_KEY,
            }:
                await asyncio.sleep(0.1)

        task.status = BrowserTaskStatus.STEP_LIMIT_REACHED
        return BrowserOutcome(
            BrowserActionState.FAILED,
            "I couldn't complete that browser task within the allowed number of steps.",
            False,
            task_id=task.task_id,
        )

    def _proposal_from_decision(
        self,
        task: BrowserTask,
        decision: BrowserDecision,
        snapshot: dict[str, object],
    ) -> BrowserActionProposal:
        selected = (
            decision.requested_action
            if decision.action == BrowserDecisionAction.REQUEST_CONFIRMATION
            else decision.action
        )
        tab_id = task.current_tab_id
        if tab_id is None or selected is None:
            raise BrowserPlannerError("The browser task has no current action target.")
        arguments: dict[str, object]
        action_map = {
            BrowserDecisionAction.CLICK: "browser_click",
            BrowserDecisionAction.TYPE: "browser_type",
            BrowserDecisionAction.SELECT: "browser_select",
            BrowserDecisionAction.SCROLL: "browser_scroll",
            BrowserDecisionAction.PRESS_KEY: "browser_press_key",
            BrowserDecisionAction.NAVIGATE: "browser_navigate",
            BrowserDecisionAction.BACK: "browser_go_back",
            BrowserDecisionAction.OPEN_TAB: "browser_open_tab",
        }
        if selected not in action_map:
            raise BrowserPlannerError("That planner decision cannot execute a browser action.")
        if selected in {
            BrowserDecisionAction.CLICK,
            BrowserDecisionAction.TYPE,
            BrowserDecisionAction.SELECT,
        }:
            arguments = {
                "tab_id": tab_id,
                "snapshot_id": str(snapshot["snapshot_id"]),
                "element_id": str(decision.element_ref),
            }
            if selected == BrowserDecisionAction.TYPE:
                arguments.update({"text": str(decision.text), "clear": True})
            elif selected == BrowserDecisionAction.SELECT:
                arguments["value"] = str(decision.value)
        elif selected == BrowserDecisionAction.SCROLL:
            arguments = {"tab_id": tab_id, "delta_y": int(decision.delta_y or 0)}
        elif selected == BrowserDecisionAction.PRESS_KEY:
            arguments = {"tab_id": tab_id, "key": str(decision.key)}
        elif selected == BrowserDecisionAction.NAVIGATE:
            arguments = {"tab_id": tab_id, "url": str(decision.url)}
        elif selected == BrowserDecisionAction.OPEN_TAB:
            arguments = {"url": str(decision.url), "active": True}
        else:
            arguments = {"tab_id": tab_id}
        return BrowserActionProposal(
            action=action_map[selected],
            arguments=arguments,
            reason=decision.reason,
            expected_result=decision.expected_result,
            external_effect=(
                decision.action == BrowserDecisionAction.REQUEST_CONFIRMATION
            ),
        )

    @staticmethod
    def _completion_verified(
        decision: BrowserDecision, snapshot: dict[str, object]
    ) -> bool:
        if not decision.answer or not decision.evidence:
            return False
        corpus = " ".join(
            str(snapshot.get(key) or "")
            for key in ("url", "title", "visible_text_summary")
        )
        corpus = " ".join(corpus.casefold().split())
        evidence = [" ".join(item.casefold().split()) for item in decision.evidence]
        return bool(evidence) and all(
            len(item) >= 3 and item in corpus for item in evidence
        )

    @staticmethod
    def _snapshot_fingerprint(snapshot: dict[str, object]) -> str:
        elements = []
        for item in snapshot.get("interactive_elements", []):
            if isinstance(item, dict):
                elements.append(
                    {
                        key: item.get(key)
                        for key in (
                            "role",
                            "tag",
                            "visible_text",
                            "accessible_name",
                            "href",
                            "disabled",
                            "checked",
                            "selected",
                        )
                    }
                )
        value = {
            "url": snapshot.get("url"),
            "title": snapshot.get("title"),
            "visible_text_summary": snapshot.get("visible_text_summary"),
            "interactive_elements": elements,
        }
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _decision_signature(
        decision: BrowserDecision,
        snapshot: Optional[dict[str, object]] = None,
    ) -> str:
        element_signature = None
        if decision.element_ref and snapshot is not None:
            element_signature = next(
                (
                    {
                        key: item.get(key)
                        for key in (
                            "role",
                            "tag",
                            "visible_text",
                            "accessible_name",
                            "input_type",
                            "href",
                        )
                    }
                    for item in snapshot.get("interactive_elements", [])
                    if isinstance(item, dict)
                    and item.get("element_id") == decision.element_ref
                ),
                None,
            )
        return json.dumps(
            {
                "action": decision.action.value,
                "requested_action": (
                    decision.requested_action.value
                    if decision.requested_action is not None
                    else None
                ),
                "element": element_signature or decision.element_ref,
                "text": decision.text,
                "url": decision.url,
                "value": decision.value,
                "key": decision.key,
                "delta_y": decision.delta_y,
            },
            sort_keys=True,
        )

    def _record_agent_action(
        self,
        task: BrowserTask,
        decision: BrowserDecision,
        snapshot: dict[str, object],
    ) -> None:
        task.last_action_signature = self._decision_signature(decision, snapshot)
        task.last_snapshot_fingerprint = self._snapshot_fingerprint(snapshot)
        task.recent_actions.append(
            f"{decision.action.value}: {decision.reason[:160]}"
        )
        task.recent_actions[:] = task.recent_actions[-5:]

    def _update_task_tab(self, task: BrowserTask, result: BrowserResult) -> None:
        task.current_tab_id = self._tab_id(result) or task.current_tab_id

    async def _search(
        self,
        query: str,
        search_engine: str,
        profile_id: str,
    ) -> BrowserOutcome:
        try:
            cleaned = validate_search_query(query)
            url = build_search_url(search_engine, cleaned)
        except ValueError as error:
            return BrowserOutcome(BrowserActionState.FAILED, str(error), False)
        selected_profile = profile_id
        disconnected = self._disconnected_profile(selected_profile)
        if disconnected:
            return replace(
                disconnected,
                evidence=BrowserExecutionEvidence(
                    requested_profile_id=selected_profile,
                    requested_url=url,
                    target_tab_id=None,
                    verified=False,
                    error_code="PROFILE_DISCONNECTED",
                ),
            )

        profile_state = self._profile_state(selected_profile)
        target_tab_id = (
            profile_state.active_tab_id
            if profile_state.active_tab_id is not None
            and (
                profile_state.last_search_engine == search_engine
                or profile_state.last_service == search_engine
            )
            else None
        )
        request = BrowserSearchRequest(
            profile_id=selected_profile,
            search_engine=search_engine,
            query=cleaned,
            target_tab_id=target_tab_id,
        )
        if request.target_tab_id is None:
            action = "browser_open_tab"
            arguments: dict[str, object] = {"url": url, "active": True}
        else:
            action = "browser_navigate"
            arguments = {"tab_id": request.target_tab_id, "url": url}
        result = await self.transport.send_request(
            selected_profile, action, arguments
        )
        self._audit_direct(
            selected_profile,
            normalized_domain(url),
            action,
            result.state.value,
            "search_registry_allow",
        )
        if (
            not result.success
            and request.target_tab_id is not None
            and result.error_code == "TAB_NOT_FOUND"
        ):
            request = BrowserSearchRequest(
                profile_id=selected_profile,
                search_engine=search_engine,
                query=cleaned,
                target_tab_id=None,
            )
            action = "browser_open_tab"
            result = await self.transport.send_request(
                selected_profile,
                action,
                {"url": url, "active": True},
            )
            self._audit_direct(
                selected_profile,
                normalized_domain(url),
                action,
                result.state.value,
                "stale_tab_recovery_allow",
            )
        if not result.success:
            return self._transport_failure(
                result,
                requested_profile_id=selected_profile,
                requested_url=url,
                target_tab_id=request.target_tab_id,
            )
        if result.profile_id != selected_profile:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser returned a result from the wrong Chrome profile.",
                False,
                target_tab_id=request.target_tab_id,
                evidence=BrowserExecutionEvidence(
                    requested_profile_id=selected_profile,
                    requested_url=url,
                    target_tab_id=request.target_tab_id,
                    request_id=result.request_id,
                    result_profile_id=result.profile_id,
                    result_url=(str(result.result.get("url")) if result.result.get("url") else None),
                    verified=False,
                    error_code="WRONG_PROFILE",
                ),
            )
        tab_id = self._tab_id(result)
        resulting_url = result.result.get("url")
        if (
            tab_id is None
            or (request.target_tab_id is not None and tab_id != request.target_tab_id)
            or not verify_search_url(resulting_url, search_engine, cleaned)
        ):
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser did not return the requested search provider and query, so I did not report success.",
                False,
                target_tab_id=request.target_tab_id,
                evidence=BrowserExecutionEvidence(
                    requested_profile_id=selected_profile,
                    requested_url=url,
                    target_tab_id=request.target_tab_id,
                    request_id=result.request_id,
                    result_profile_id=result.profile_id,
                    result_url=(str(resulting_url) if resulting_url else None),
                    result_tab_id=tab_id,
                    verified=False,
                    error_code="DESTINATION_MISMATCH",
                ),
            )
        context = BrowserActionContext(
            profile_id=selected_profile,
            service=search_engine,
            search_engine=search_engine,
            query=cleaned,
            tab_id=tab_id,
            url=str(resulting_url),
            connection_instance_id=result.connection_instance_id,
        )
        self._update_profile_state(context, "search")
        provider_name = search_provider_display_name(search_engine)
        profile_name = self._profile_name(selected_profile)
        return BrowserOutcome(
            BrowserActionState.VERIFIED,
            f"Searching {provider_name} for “{cleaned}” in your {profile_name} Chrome profile.",
            True,
            True,
            context=context,
            target_tab_id=request.target_tab_id,
            evidence=BrowserExecutionEvidence(
                requested_profile_id=selected_profile,
                requested_url=url,
                target_tab_id=request.target_tab_id,
                request_id=result.request_id,
                result_profile_id=result.profile_id,
                result_url=str(resulting_url),
                result_tab_id=tab_id,
                verified=True,
            ),
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
            context = BrowserActionContext(
                profile_id="personal",
                service="youtube",
                search_engine="youtube",
                query=cleaned_target,
                tab_id=task.current_tab_id,
                url=str(final_page.get("url") or ""),
                connection_instance_id=active.connection_instance_id,
            )
            self._update_profile_state(context, "open_youtube_channel")
            return BrowserOutcome(
                BrowserActionState.VERIFIED,
                f"Opened {cleaned_target}’s YouTube channel.",
                True,
                True,
                task.task_id,
                context=context,
            )
        task.status = BrowserTaskStatus.FAILED
        return self._failed_search(cleaned_target, task)

    async def stop_task(self) -> BrowserOutcome:
        tasks = list(self.task_manager.current_tasks_by_profile.values())
        task = self.task_manager.current_task
        profile_ids = (
            [item.profile_id for item in tasks]
            if tasks
            else [profile.profile_id for profile in self.connected_profiles()]
        )
        if not profile_ids:
            return BrowserOutcome(
                BrowserActionState.CANCELLED,
                "There is no connected browser profile to stop.",
                True,
            )
        for profile_id in profile_ids:
            profile_task = self.task_manager.current_for_profile(profile_id)
            arguments = (
                {"task_id": profile_task.task_id}
                if profile_task is not None
                else {}
            )
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
        for active_task in tasks:
            self.task_manager.stop(active_task)
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
            count_step=False,
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
        confirmed: bool = False,
        count_step: bool = True,
    ):
        decision = self.policy.evaluate(
            task, proposal, snapshot, confirmed=confirmed
        )
        if decision.decision == PolicyDecisionType.REQUIRE_CONFIRMATION:
            task.status = BrowserTaskStatus.WAITING_FOR_CONFIRMATION
            if approval_callback is None:
                self._audit(
                    task,
                    proposal,
                    "paused",
                    "pending",
                    decision.decision.value,
                    snapshot,
                )
                prompt = decision.confirmation_prompt or "Continue with that browser action?"
                return BrowserOutcome(
                    BrowserActionState.REQUESTED,
                    prompt,
                    False,
                    task_id=task.task_id,
                    confirmation_prompt=prompt,
                )
            answer = approval_callback(
                decision.confirmation_prompt or "Continue? [y/N] "
            )
            if answer.strip().casefold() not in {"y", "yes"}:
                self._audit(
                    task,
                    proposal,
                    "cancelled",
                    "declined",
                    decision.decision.value,
                    snapshot,
                )
                return BrowserOutcome(
                    BrowserActionState.CANCELLED,
                    "Browser action cancelled. No page action was performed.",
                    False,
                    task_id=task.task_id,
                )
            confirmed = True
            decision = self.policy.evaluate(
                task, proposal, snapshot, confirmed=True
            )
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
        if count_step and not self.task_manager.record_step(task):
            return BrowserOutcome(
                BrowserActionState.FAILED,
                f"I have not verified the requested result after {task.max_steps} browser actions. Continue for another {self.task_manager.step_extension} actions or stop?",
                False,
                task_id=task.task_id,
            )
        if not count_step:
            task.status = BrowserTaskStatus.RUNNING
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
        if result.profile_id != task.profile_id:
            task.status = BrowserTaskStatus.FAILED
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser returned a result from the wrong Chrome profile.",
                False,
                task_id=task.task_id,
            )
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
            f"Your {label} Chrome profile is not connected.",
            False,
        )

    def _profile_name(self, profile_id: str) -> str:
        for profile in self.connected_profiles():
            if profile.profile_id == profile_id:
                return profile.profile_name
        return "NYU" if profile_id == "nyu" else "Personal"

    def _profile_state(self, profile_id: str) -> ProfileBrowserState:
        state = self.browser_state_by_profile.get(profile_id)
        if state is None:
            state = ProfileBrowserState(profile_id=profile_id)
            self.browser_state_by_profile[profile_id] = state
        return state

    def _update_profile_state(
        self, context: BrowserActionContext, successful_action: str
    ) -> None:
        state = self._profile_state(context.profile_id)
        state.active_tab_id = context.tab_id
        state.last_service = context.service
        state.last_search_engine = context.search_engine
        if context.query is not None:
            state.last_search_query = context.query
        state.last_successful_action = successful_action
        state.last_url = context.url

    @staticmethod
    def _verified_tab_result(
        result: BrowserResult,
        profile_id: str,
        expected_url: str,
    ) -> tuple[int, str] | BrowserOutcome:
        if result.profile_id != profile_id:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser returned a result from the wrong Chrome profile.",
                False,
                evidence=BrowserExecutionEvidence(
                    requested_profile_id=profile_id,
                    requested_url=expected_url,
                    request_id=result.request_id,
                    result_profile_id=result.profile_id,
                    result_url=(str(result.result.get("url")) if result.result.get("url") else None),
                    verified=False,
                    error_code="WRONG_PROFILE",
                ),
            )
        tab_id = BrowserCopilot._tab_id(result)
        resulting_url = result.result.get("url")
        try:
            destination_matches = (
                normalized_domain(resulting_url) == normalized_domain(expected_url)
            )
        except BrowserSecurityError:
            destination_matches = False
        if tab_id is None or not isinstance(resulting_url, str) or not destination_matches:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "The browser did not verify the requested destination, so I did not report success.",
                False,
                evidence=BrowserExecutionEvidence(
                    requested_profile_id=profile_id,
                    requested_url=expected_url,
                    request_id=result.request_id,
                    result_profile_id=result.profile_id,
                    result_url=(str(resulting_url) if resulting_url else None),
                    result_tab_id=tab_id,
                    verified=False,
                    error_code="DESTINATION_MISMATCH",
                ),
            )
        return tab_id, resulting_url

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
        result: BrowserResult,
        task_id: Optional[str] = None,
        *,
        requested_profile_id: Optional[str] = None,
        requested_url: str = "",
        target_tab_id: Optional[int] = None,
    ) -> BrowserOutcome:
        evidence = None
        if requested_profile_id is not None:
            evidence = BrowserExecutionEvidence(
                requested_profile_id=requested_profile_id,
                requested_url=requested_url,
                target_tab_id=target_tab_id,
                request_id=result.request_id,
                result_profile_id=result.profile_id,
                result_url=(str(result.result.get("url")) if result.result.get("url") else None),
                result_tab_id=BrowserCopilot._tab_id(result),
                verified=False,
                error_code=result.error_code,
            )
        return BrowserOutcome(
            result.state,
            result.error or "The browser action failed.",
            False,
            False,
            task_id,
            evidence=evidence,
        )
