"""Background event-loop runtime for the local browser bridge and copilot."""

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import re
import threading
from typing import Coroutine, Optional

from sabel.browser_copilot import BrowserCopilot, BrowserOutcome
from sabel.browser_models import BrowserActionState
from sabel.browser_security import BrowserTokenStore, ExtensionOriginStore
from sabel.browser_tasks import BrowserAuditLog, BrowserTaskManager
from sabel.browser_transport import BrowserTransport, WebSocketBrowserTransport
from sabel.config import Settings


class BrowserBridgeRuntime:
    """Run async WebSocket work without making SABEL's terminal loop async."""

    def __init__(
        self,
        settings: Settings,
        transport: Optional[BrowserTransport] = None,
        *,
        operation_timeout: float = 120.0,
    ) -> None:
        self.settings = settings
        self.token_store = BrowserTokenStore(settings.browser_token_path)
        self.origin_store = ExtensionOriginStore(settings.browser_origin_path)
        self.transport = transport or WebSocketBrowserTransport(
            port=settings.browser_bridge_port,
            token_store=self.token_store,
            origin_store=self.origin_store,
        )
        self.audit_log = BrowserAuditLog(path=settings.browser_audit_path)
        self.copilot = BrowserCopilot(
            self.transport,
            task_manager=BrowserTaskManager(max_steps=settings.browser_max_actions),
            audit_log=self.audit_log,
            albert_url=settings.albert_url,
            default_gmail_profile=settings.default_gmail_profile,
        )
        self.operation_timeout = operation_timeout
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        self._loop = asyncio.new_event_loop()

        def run_loop() -> None:
            assert self._loop is not None
            asyncio.set_event_loop(self._loop)
            self._ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(
            target=run_loop,
            name="sabel-browser-bridge",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("The browser bridge event loop did not start.")
        try:
            self._run(self.transport.start(), timeout=10.0)
        except Exception:
            self._stop_loop_only()
            raise
        self.started = True

    def stop(self) -> None:
        if self._loop is None:
            return
        if self.started:
            try:
                self._run(self.transport.stop(), timeout=10.0)
            except Exception:
                pass
        self.started = False
        self._stop_loop_only()

    def register_extension_id(self, extension_id: str) -> str:
        return self.origin_store.register_extension_id(extension_id)

    def rotate_token(self) -> None:
        if self.started:
            rotate = getattr(self.transport, "rotate_token", None)
            if rotate is None:
                raise RuntimeError("This browser transport cannot rotate tokens.")
            self._run(rotate(), timeout=10.0)
        else:
            self.token_store.rotate()

    def show_profiles(self) -> BrowserOutcome:
        if not self.started:
            return self._unavailable()
        return self.copilot.show_profiles()

    def open_service_in_profile(
        self, service: str, profile: Optional[str] = None
    ) -> BrowserOutcome:
        if not self.started:
            return self._unavailable()
        return self._run(self.copilot.open_service(service, profile))

    def show_browser_tabs(self, profile: str) -> BrowserOutcome:
        if not self.started:
            return self._unavailable()
        return self._run(self.copilot.show_tabs(profile))

    def browser_search(
        self, service: str, query: str, profile: Optional[str] = None
    ) -> BrowserOutcome:
        if not self.started:
            return self._unavailable()
        return self._run(self.copilot.browser_search(service, query, profile))

    def browser_copilot_task(
        self, objective: str, service: str, profile: str, approval_callback=None
    ) -> BrowserOutcome:
        if not self.started:
            return self._unavailable()
        if service.casefold() != "youtube":
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "Browser Copilot v1 currently supports verified channel navigation on YouTube.",
                False,
            )
        if profile.casefold() != "personal":
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "YouTube channel tasks use the Personal browser profile in Browser Copilot v1.",
                False,
            )
        target = _youtube_channel_target(objective)
        if not target:
            return BrowserOutcome(
                BrowserActionState.FAILED,
                "Please name the YouTube channel you want to open.",
                False,
            )
        return self._run(
            self.copilot.open_youtube_channel(
                target,
                objective,
                approval_callback=approval_callback,
            )
        )

    def stop_browser_task(self) -> BrowserOutcome:
        if not self.started:
            return self._unavailable()
        return self._run(self.copilot.stop_task())

    def show_recent_browser_actions(self) -> BrowserOutcome:
        return BrowserOutcome(
            BrowserActionState.VERIFIED,
            self.audit_log.concise_summary(),
            True,
            True,
        )

    def _run(self, coroutine: Coroutine, timeout: Optional[float] = None):
        if self._loop is None or not self._loop.is_running():
            coroutine.close()
            raise RuntimeError("The browser bridge is not running.")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout or self.operation_timeout)
        except FutureTimeoutError as error:
            future.cancel()
            raise RuntimeError("The browser operation timed out.") from error

    def _stop_loop_only(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5.0)
        if loop is not None and not loop.is_closed():
            loop.close()
        self._loop = None
        self._thread = None
        self._ready.clear()

    @staticmethod
    def _unavailable() -> BrowserOutcome:
        return BrowserOutcome(
            BrowserActionState.FAILED,
            "Browser Copilot is unavailable because the local bridge is not running.",
            False,
        )


def _youtube_channel_target(objective: str) -> Optional[str]:
    cleaned = " ".join(objective.strip().split()).strip(" .!?")
    match = re.search(
        r"^(?:(?:open|go to|take me to|navigate to|pull up)\s+)?(.+?)(?:['’]s)?\s+youtube\s+channel$",
        cleaned,
        re.I,
    )
    if not match:
        return None
    target = match.group(1).strip(" .!?")
    return target[:200] if target else None
