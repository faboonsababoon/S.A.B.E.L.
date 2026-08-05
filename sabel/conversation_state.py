"""Small, bounded session state; nothing is persisted between runs."""

from dataclasses import dataclass, field
import time
from typing import Dict, List, Optional, Tuple

from sabel.browser_models import BrowserActionContext, LastBrowserReference
from sabel.request_models import (
    ActionRecord,
    ActionResultStatus,
    BrowserExecutionEvidence,
    Intent,
    PendingMediaRequest,
    ResolvedRequest,
    ReusableActionTemplate,
)


@dataclass(frozen=True)
class PendingDestructiveAction:
    tool_name: str
    arguments: Dict[str, object]
    description: str
    warning: str
    created_at: float


@dataclass(frozen=True)
class PendingClarification:
    original_request: str
    question: str
    intent: str
    collected_slots: Dict[str, object]
    missing_slots: List[str]
    created_at: float
    proposed_slots: Dict[str, object] = field(default_factory=dict)

    @property
    def expected_slot(self) -> Optional[str]:
        """Compatibility view for older callers; state remains slot-structured."""
        return self.missing_slots[0] if self.missing_slots else None


@dataclass(frozen=True)
class PendingCloudFallback:
    original_request: str
    task: str
    requires_current_web_information: bool
    created_at: float


@dataclass(frozen=True)
class ActionResultRecord:
    tool_name: str
    success: bool
    message: str
    created_at: float


@dataclass
class ConversationState:
    history_limit: int
    pending_action_ttl: float = 60.0
    clarification_ttl: float = 60.0
    browser_reference_ttl: float = 300.0
    pending_destructive_action: Optional[PendingDestructiveAction] = None
    pending_clarification: Optional[PendingClarification] = None
    pending_cloud_fallback: Optional[PendingCloudFallback] = None
    pending_media_request: Optional[PendingMediaRequest] = None
    recent_history: List[Dict[str, str]] = field(default_factory=list)
    last_action_result: Optional[ActionResultRecord] = None
    last_attempted_action: Optional[ActionRecord] = None
    last_successful_action: Optional[ActionRecord] = None
    reusable_action: Optional[ReusableActionTemplate] = None
    last_browser_reference: Optional[LastBrowserReference] = None
    last_browser_query: Optional[str] = None
    research_answer: Optional[str] = None
    research_sources: List[str] = field(default_factory=list)
    last_unverified_local_fallback_task: Optional[str] = None

    def add_exchange(self, user_text: str, assistant_text: str) -> None:
        self.recent_history.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text[:2000]},
            ]
        )
        self.recent_history = self.recent_history[-self.history_limit :]

    def messages(self) -> List[Dict[str, str]]:
        return list(self.recent_history)

    def set_pending_destructive_action(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, object]] = None,
        description: str = "",
        warning: str = "",
        created_at: Optional[float] = None,
    ) -> None:
        self.pending_destructive_action = PendingDestructiveAction(
            tool_name=tool_name,
            arguments=dict(arguments or {}),
            description=description,
            warning=warning,
            created_at=time.monotonic() if created_at is None else created_at,
        )

    def clear_pending_destructive_action(self) -> None:
        self.pending_destructive_action = None

    def pending_expired(self, now: Optional[float] = None) -> bool:
        if self.pending_destructive_action is None:
            return False
        current = time.monotonic() if now is None else now
        return (
            current - self.pending_destructive_action.created_at
            >= self.pending_action_ttl
        )

    def clear_expired_pending_destructive_action(
        self, now: Optional[float] = None
    ) -> Optional[PendingDestructiveAction]:
        if not self.pending_expired(now):
            return None
        expired = self.pending_destructive_action
        self.pending_destructive_action = None
        return expired

    def set_pending_clarification(
        self,
        original_request: str,
        question: str,
        expected_slot: Optional[str] = None,
        created_at: Optional[float] = None,
        *,
        intent: str = "",
        collected_slots: Optional[Dict[str, object]] = None,
        missing_slots: Optional[List[str]] = None,
        proposed_slots: Optional[Dict[str, object]] = None,
    ) -> None:
        unresolved = list(missing_slots or ([expected_slot] if expected_slot else []))
        self.pending_clarification = PendingClarification(
            original_request=original_request,
            question=question,
            intent=intent,
            collected_slots=dict(collected_slots or {}),
            missing_slots=unresolved,
            created_at=time.monotonic() if created_at is None else created_at,
            proposed_slots=dict(proposed_slots or {}),
        )

    def clear_pending_clarification(self) -> None:
        self.pending_clarification = None

    def clarification_expired(self, now: Optional[float] = None) -> bool:
        if self.pending_clarification is None:
            return False
        current = time.monotonic() if now is None else now
        return (
            current - self.pending_clarification.created_at
            >= self.clarification_ttl
        )

    def clear_expired_clarification(
        self, now: Optional[float] = None
    ) -> Optional[PendingClarification]:
        if not self.clarification_expired(now):
            return None
        expired = self.pending_clarification
        self.pending_clarification = None
        return expired

    def record_action_result(
        self, tool_name: str, success: bool, message: str
    ) -> None:
        self.last_action_result = ActionResultRecord(
            tool_name=tool_name,
            success=success,
            message=message[:1000],
            created_at=time.monotonic(),
        )

    def begin_action(self, request: ResolvedRequest) -> None:
        """Record an execution attempt before any external side effect begins."""
        self.last_attempted_action = ActionRecord.attempted(request)

    def reject_last_attempt(self) -> None:
        if self.last_attempted_action is not None:
            rejected_request = self.last_attempted_action.request
            self.last_attempted_action = self.last_attempted_action.rejected()
            if (
                self.last_successful_action is not None
                and self.last_successful_action.request == rejected_request
            ):
                self.reusable_action = None

    def complete_action(
        self,
        request: ResolvedRequest,
        *,
        success: bool,
        verified: bool,
        message: str,
        browser_context: Optional[BrowserActionContext] = None,
        browser_debug: Optional[Dict[str, object]] = None,
    ) -> None:
        """Complete one attempt and update reusable context only after verification."""
        evidence = None
        if request.profile_id:
            debug = browser_debug or {}
            evidence = BrowserExecutionEvidence(
                requested_profile_id=request.profile_id,
                requested_url=str(debug.get("requested_url") or debug.get("generated_url") or ""),
                target_tab_id=(
                    debug.get("target_tab") if isinstance(debug.get("target_tab"), int) else None
                ),
                request_id=(str(debug["request_id"]) if debug.get("request_id") else None),
                result_profile_id=(
                    browser_context.profile_id
                    if browser_context is not None
                    else str(debug["result_profile"]) if debug.get("result_profile") else None
                ),
                result_url=(
                    browser_context.url
                    if browser_context is not None
                    else str(debug["result_url"]) if debug.get("result_url") else None
                ),
                result_tab_id=browser_context.tab_id if browser_context is not None else None,
                verified=verified,
                error_code=(str(debug["error_code"]) if debug.get("error_code") else None),
            )
        browser_intent = request.intent in {
            Intent.OPEN_SERVICE,
            Intent.SEARCH_WEB,
            Intent.SEARCH_YOUTUBE,
        }
        accepted_success = success and (verified if browser_intent else True)
        status = ActionResultStatus.VERIFIED if accepted_success else ActionResultStatus.FAILED
        record = ActionRecord(
            request=request,
            result=status,
            visible_message=message[:1000],
            browser_evidence=evidence,
            timestamp=time.monotonic(),
        )
        self.last_attempted_action = record
        if accepted_success:
            self.last_successful_action = record
            self.reusable_action = ReusableActionTemplate.from_request(request)

    def contextual_failure_message(self) -> str:
        record = self.last_attempted_action
        if record is None:
            return "I do not have a recent attempted action to inspect."
        request = record.request
        profile = (
            "NYU" if request.profile_id == "nyu" else "Personal"
            if request.profile_id == "personal" else None
        )
        if request.intent in {Intent.SEARCH_WEB, Intent.SEARCH_YOUTUBE}:
            provider = "YouTube" if request.search_engine == "youtube" else (
                (request.search_engine or "browser").title()
            )
            action = f"a {provider} search for “{request.query or ''}”"
            if profile:
                action += f" in your {profile} profile"
        elif request.intent == Intent.OPEN_SERVICE:
            action = f"opening {(request.service or 'the service').title()}"
            if profile:
                action += f" in your {profile} profile"
        elif request.intent == Intent.OPEN_APPLICATION:
            action = f"opening the {request.application_name or 'requested'} application"
        else:
            action = "the previous action"
        if record.result == ActionResultStatus.VERIFIED:
            return (
                f"The last action was {action}. SABEL received a verified success result, "
                "but you are saying it was not visible. I can retry it; for a browser action, "
                "I can also verify the exact profile and destination again."
            )
        return f"The last attempted action was {action}, and it did not verify successfully. {record.visible_message}".strip()

    def set_pending_media_request(
        self,
        *,
        query: str,
        service: Optional[str],
        profile_id: Optional[str],
        missing_fields: set[str],
        proposed_values: Optional[Dict[str, object]] = None,
        created_at: Optional[float] = None,
    ) -> None:
        self.pending_media_request = PendingMediaRequest(
            query=query,
            service=service,
            profile_id=profile_id,
            missing_fields=frozenset(missing_fields),
            proposed_values=tuple(sorted((proposed_values or {}).items())),
            created_at=time.monotonic() if created_at is None else created_at,
        )

    def clear_pending_media_request(self) -> None:
        self.pending_media_request = None

    def clear_expired_media_request(
        self, now: Optional[float] = None
    ) -> Optional[PendingMediaRequest]:
        pending = self.pending_media_request
        if pending is None:
            return None
        current = time.monotonic() if now is None else now
        if current - pending.created_at < self.clarification_ttl:
            return None
        self.pending_media_request = None
        return pending

    def action_context(self) -> str:
        if self.last_action_result is None:
            return "No local action result is available."
        status = "succeeded" if self.last_action_result.success else "failed"
        return (
            f"Last local action {status}. Visible result: "
            f"{self.last_action_result.message}"
        )

    def record_browser_context(
        self,
        service: Optional[str],
        profile: Optional[str],
        query: Optional[str] = None,
    ) -> None:
        """Compatibility helper for callers with validated, but unverified, test data."""
        if profile in {"personal", "nyu"}:
            self.last_browser_reference = LastBrowserReference(
                profile_id=profile,
                service=service,
                search_engine=service if service in {"google", "youtube"} else None,
                tab_id=None,
                completed_at=time.monotonic(),
            )
        else:
            self.last_browser_reference = None
        if isinstance(query, str):
            self.last_browser_query = query

    def record_verified_browser_context(
        self,
        context: BrowserActionContext,
        *,
        completed_at: Optional[float] = None,
    ) -> None:
        """Update conversational browser context only from verified execution evidence."""
        self.last_browser_reference = LastBrowserReference(
            profile_id=context.profile_id,
            service=context.service,
            search_engine=context.search_engine,
            tab_id=context.tab_id,
            completed_at=(
                time.monotonic() if completed_at is None else completed_at
            ),
        )
        if context.query is not None:
            self.last_browser_query = context.query

    def current_browser_reference(
        self, now: Optional[float] = None
    ) -> Optional[LastBrowserReference]:
        reference = self.last_browser_reference
        if reference is None:
            return None
        current = time.monotonic() if now is None else now
        if current - reference.completed_at >= self.browser_reference_ttl:
            self.last_browser_reference = None
            return None
        return reference

    @property
    def last_browser_service(self) -> Optional[str]:
        reference = self.current_browser_reference()
        return reference.service if reference else None

    @property
    def last_browser_profile(self) -> Optional[str]:
        reference = self.current_browser_reference()
        return reference.profile_id if reference else None

    def clear_all_pending(self) -> None:
        self.pending_destructive_action = None
        self.pending_clarification = None
        self.pending_cloud_fallback = None
        self.pending_media_request = None

    def set_pending_cloud_fallback(
        self,
        original_request: str,
        task: str,
        requires_current_web_information: bool,
        created_at: Optional[float] = None,
    ) -> None:
        self.pending_cloud_fallback = PendingCloudFallback(
            original_request=original_request,
            task=task,
            requires_current_web_information=requires_current_web_information,
            created_at=time.monotonic() if created_at is None else created_at,
        )

    def clear_pending_cloud_fallback(self) -> None:
        self.pending_cloud_fallback = None

    def cloud_fallback_expired(self, now: Optional[float] = None) -> bool:
        if self.pending_cloud_fallback is None:
            return False
        current = time.monotonic() if now is None else now
        return current - self.pending_cloud_fallback.created_at >= self.clarification_ttl

    def clear_expired_cloud_fallback(
        self, now: Optional[float] = None
    ) -> Optional[PendingCloudFallback]:
        if not self.cloud_fallback_expired(now):
            return None
        expired = self.pending_cloud_fallback
        self.pending_cloud_fallback = None
        return expired

    def store_research(self, answer: str, sources: List[str]) -> None:
        # A new research result predictably replaces the previous one.
        self.research_answer = answer
        self.research_sources = list(sources[:20])
        self.last_unverified_local_fallback_task = None

    def resolve_source(self, one_based_index: int) -> Tuple[Optional[str], str]:
        if one_based_index < 1 or one_based_index > len(self.research_sources):
            return None, "That source number is not available in the latest research."
        return self.research_sources[one_based_index - 1], ""

    def research_context(self) -> str:
        if not self.research_answer:
            return "No cloud research result is stored."
        return (
            "Latest research excerpt:\n"
            f"{self.research_answer[:2000]}\n"
            f"Stored numbered sources: {len(self.research_sources)}"
        )
