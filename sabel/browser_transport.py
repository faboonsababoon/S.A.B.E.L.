"""Browser transport abstraction and authenticated localhost WebSocket server."""

import asyncio
from dataclasses import dataclass, field
from http import HTTPStatus
import json
import logging
import time
from typing import Any, Callable, Optional, Protocol
import uuid

from sabel.browser_models import BrowserActionState, BrowserProfile, BrowserResult
from sabel.browser_protocol import (
    ProtocolError,
    command_message,
    decode_json,
    server_message,
    validate_action,
    validate_client_message,
    validate_register,
)
from sabel.browser_security import (
    BrowserTokenStore,
    ExtensionOriginStore,
    SlidingWindowRateLimiter,
)


class BrowserTransport(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_request(
        self,
        profile_id: str,
        action: str,
        arguments: dict[str, object],
    ) -> BrowserResult: ...
    def connected_profiles(self) -> list[BrowserProfile]: ...


@dataclass
class BrowserProfileConnection:
    """One fully authenticated extension connection for one exact profile."""

    websocket: Any
    profile: BrowserProfile
    limiter: SlidingWindowRateLimiter
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))
    last_heartbeat: float = field(default_factory=time.time)

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    @property
    def profile_name(self) -> str:
        return self.profile.profile_name

    @property
    def instance_id(self) -> str:
        return self.profile.instance_id

    @property
    def connected_at(self) -> float:
        return self.profile.connected_at

    @property
    def extension_version(self) -> str:
        return self.profile.extension_version


# Compatibility name for older tests and integrations.
_ConnectionState = BrowserProfileConnection


class WebSocketBrowserTransport:
    """Serve authenticated extension clients on the IPv4 loopback interface only."""

    HOST = "127.0.0.1"

    def __init__(
        self,
        *,
        port: int,
        token_store: BrowserTokenStore,
        origin_store: ExtensionOriginStore,
        max_message_size: int = 262_144,
        max_queue: int = 32,
        registration_timeout: float = 5.0,
        command_timeout: float = 15.0,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
        max_concurrent_requests: int = 4,
        rate_limit_count: int = 30,
        rate_limit_window: float = 10.0,
        serve_factory: Optional[Callable[..., Any]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("Browser bridge port must be between 1 and 65535.")
        self.port = port
        self.token_store = token_store
        self.origin_store = origin_store
        self.max_message_size = max_message_size
        self.max_queue = max_queue
        self.registration_timeout = registration_timeout
        self.command_timeout = command_timeout
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.max_concurrent_requests = max_concurrent_requests
        self.rate_limit_count = rate_limit_count
        self.rate_limit_window = rate_limit_window
        self.serve_factory = serve_factory
        self.logger = logger or logging.getLogger("sabel.browser_bridge")
        self._server = None
        self._token: Optional[str] = None
        self.connections: dict[str, BrowserProfileConnection] = {}
        self._connections = self.connections
        self._profile_snapshot: tuple[BrowserProfile, ...] = ()
        self._registry_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._token = self.token_store.load_or_create()
        serve_factory = self.serve_factory
        if serve_factory is None:
            try:
                from websockets.asyncio.server import serve
            except ImportError as error:
                raise RuntimeError(
                    "The websockets package is missing. Run: python3 -m pip install -r requirements.txt"
                ) from error
            serve_factory = serve
        self._server = await serve_factory(
            self._handle_connection,
            self.HOST,
            self.port,
            process_request=self._process_request,
            max_size=self.max_message_size,
            max_queue=self.max_queue,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
        )
        self.logger.info("browser_bridge_started host=%s port=%s", self.HOST, self.port)

    async def stop(self) -> None:
        await self.disconnect_all("Browser bridge stopped.")
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def rotate_token(self) -> None:
        self._token = self.token_store.rotate()
        await self.disconnect_all("Browser authentication token rotated.")
        self.logger.info("browser_token_rotated")

    def connected_profiles(self) -> list[BrowserProfile]:
        return list(self._profile_snapshot)

    def _refresh_profile_snapshot_locked(self) -> None:
        order = {"personal": 0, "nyu": 1}
        self._profile_snapshot = tuple(
            sorted(
                (state.profile for state in self.connections.values()),
                key=lambda profile: (order.get(profile.profile_id, 99), profile.profile_id),
            )
        )

    async def _process_request(self, connection, request):
        origin = request.headers.get("Origin")
        if not self.origin_store.is_allowed(origin):
            self.logger.warning("browser_invalid_origin_rejected")
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden extension origin.\n")
        return None

    async def _handle_connection(self, websocket) -> None:
        state: Optional[_ConnectionState] = None
        try:
            try:
                raw = await asyncio.wait_for(websocket.recv(), self.registration_timeout)
            except asyncio.TimeoutError:
                self.logger.warning("browser_authentication_timeout")
                await websocket.close(code=4408, reason="Registration timeout")
                return
            registration = validate_register(self._decode_limited(raw))
            if self._token is None or not self.token_store.matches(
                self._token, registration["token"]
            ):
                self.logger.warning("browser_authentication_failure")
                await websocket.close(code=4401, reason="Authentication failed")
                return
            profile = BrowserProfile(
                profile_id=str(registration["profile_id"]),
                profile_name=str(registration["profile_name"]),
                instance_id=str(registration["instance_id"]),
                extension_version=str(registration["extension_version"]),
            )
            state = BrowserProfileConnection(
                websocket=websocket,
                profile=profile,
                limiter=SlidingWindowRateLimiter(
                    self.rate_limit_count, self.rate_limit_window
                ),
                semaphore=asyncio.Semaphore(self.max_concurrent_requests),
            )
            registered = await self._register_connection(
                state, registration_request_id=str(registration["request_id"])
            )
            if not registered:
                self.logger.warning(
                    "browser_profile_conflict profile=%s", profile.profile_id
                )
                await websocket.close(
                    code=4001,
                    reason="Profile ID already belongs to another extension instance",
                )
                return
            async for raw_message in websocket:
                if not state.limiter.allow():
                    self.logger.warning(
                        "browser_rate_limit profile=%s", state.profile.profile_id
                    )
                    await websocket.close(code=4429, reason="Rate limit exceeded")
                    return
                try:
                    message = validate_client_message(self._decode_limited(raw_message))
                    await self._handle_authenticated_message(state, message)
                except ProtocolError as error:
                    self.logger.warning(
                        "browser_malformed_message profile=%s code=%s",
                        state.profile.profile_id,
                        error.code,
                    )
                    await websocket.close(code=4400, reason=error.code)
                    return
        except ProtocolError as error:
            self.logger.warning("browser_malformed_registration code=%s", error.code)
            await websocket.close(code=4400, reason=error.code)
        except Exception as error:
            if (
                error.__class__.__module__.startswith("websockets")
                and error.__class__.__name__.startswith("ConnectionClosed")
            ):
                self.logger.info("browser_connection_closed")
            else:
                self.logger.warning(
                    "browser_connection_error type=%s",
                    error.__class__.__name__,
                    exc_info=False,
                )
        finally:
            if state is not None:
                await self._remove_connection(state)

    def _decode_limited(self, raw: object) -> dict[str, object]:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > self.max_message_size:
            raise ProtocolError("Browser message exceeds the size limit.", "MESSAGE_TOO_LARGE")
        return decode_json(raw)

    async def _register_connection(
        self,
        state: BrowserProfileConnection,
        registration_request_id: Optional[str] = None,
    ) -> bool:
        """Atomically register after authentication.

        A reconnect from the same extension instance replaces its stale socket.
        A different instance claiming an occupied profile ID is rejected so one
        misconfigured Chrome profile cannot evict another installation.
        """
        previous = None
        async with self._registry_lock:
            previous = self.connections.get(state.profile_id)
            if previous is not None and previous.instance_id != state.instance_id:
                return False
            if registration_request_id is not None:
                await state.websocket.send(
                    json.dumps(
                        server_message(
                            "registered",
                            registration_request_id,
                            profile_id=state.profile_id,
                            profile_name=state.profile_name,
                        )
                    )
                )
            self.connections[state.profile_id] = state
            self._refresh_profile_snapshot_locked()
        if previous is not None and previous is not state:
            self._fail_pending(previous, "Browser profile reconnected.", "PROFILE_REPLACED")
            await previous.websocket.close(code=4002, reason="Newer instance connection")
        self.logger.info(
            "browser_profile_connected profile=%s", state.profile.profile_id
        )
        return True

    async def _remove_connection(self, state: BrowserProfileConnection) -> None:
        async with self._registry_lock:
            if self.connections.get(state.profile.profile_id) is state:
                self.connections.pop(state.profile.profile_id, None)
                self._refresh_profile_snapshot_locked()
        self._fail_pending(state, "Browser profile disconnected.", "PROFILE_DISCONNECTED")
        self.logger.info(
            "browser_profile_disconnected profile=%s", state.profile.profile_id
        )

    def _fail_pending(
        self, state: BrowserProfileConnection, message: str, code: str
    ) -> None:
        for future in list(state.pending.values()):
            if not future.done():
                future.set_result(BrowserResult.failed(message, code))
        state.pending.clear()

    async def _handle_authenticated_message(
        self, state: BrowserProfileConnection, message: dict[str, object]
    ) -> None:
        if message["profile_id"] != state.profile.profile_id:
            raise ProtocolError("Profile ID does not match this connection.", "PROFILE_MISMATCH")
        if message["type"] == "heartbeat":
            state.last_heartbeat = time.time()
            await state.websocket.send(
                json.dumps(
                    server_message(
                        "heartbeat_ack",
                        str(message["request_id"]),
                        profile_id=state.profile.profile_id,
                    )
                )
            )
            return
        if message["type"] == "event":
            if message["event"] in {"browser_control_stopped", "task_cancelled"}:
                self._fail_pending(state, "Browser control was stopped.", "CANCELLED")
            return
        request_id = str(message["request_id"])
        future = state.pending.get(request_id)
        if future is None or future.done():
            raise ProtocolError("Unknown or duplicate request ID.", "DUPLICATE_REQUEST_ID")
        error = message["error"]
        if message["success"]:
            result_data = dict(message["result"])
            verified = result_data.get("verified") is True
            result = BrowserResult(
                BrowserActionState.VERIFIED if verified else BrowserActionState.EXECUTED,
                True,
                verified=verified,
                result=result_data,
                request_id=request_id,
                profile_id=state.profile_id,
                connection_instance_id=state.instance_id,
            )
        else:
            error_data = error if isinstance(error, dict) else {}
            result = BrowserResult.failed(
                str(error_data.get("message") or "The browser action failed."),
                str(error_data.get("code") or "BROWSER_ACTION_FAILED"),
            )
        future.set_result(result)

    async def send_request(
        self,
        profile_id: str,
        action: str,
        arguments: dict[str, object],
    ) -> BrowserResult:
        try:
            validate_action(action, arguments)
        except ProtocolError as error:
            self.logger.warning("browser_unknown_action code=%s", error.code)
            return BrowserResult.failed(str(error), error.code)
        async with self._registry_lock:
            state = self.connections.get(profile_id)
        if state is None:
            label = (
                "NYU"
                if profile_id == "nyu"
                else "Personal"
                if profile_id == "personal"
                else profile_id
            )
            return BrowserResult.failed(
                f"Your {label} Chrome profile is not connected.",
                "PROFILE_DISCONNECTED",
            )
        async with state.semaphore:
            request_id = f"request-{uuid.uuid4().hex}"
            if request_id in state.pending:
                return BrowserResult.failed("Duplicate request ID.", "DUPLICATE_REQUEST_ID")
            future = asyncio.get_running_loop().create_future()
            state.pending[request_id] = future
            try:
                await state.websocket.send(
                    json.dumps(command_message(request_id, profile_id, action, arguments))
                )
                try:
                    result = await asyncio.wait_for(future, self.command_timeout)
                except asyncio.TimeoutError:
                    self.logger.warning(
                        "browser_command_timeout profile=%s action=%s",
                        profile_id,
                        action,
                    )
                    return BrowserResult.failed(
                        "The browser did not respond before the command timed out.",
                        "COMMAND_TIMEOUT",
                    )
                return result
            except Exception:
                return BrowserResult.failed(
                    "The browser profile disconnected before the command completed.",
                    "PROFILE_DISCONNECTED",
                )
            finally:
                state.pending.pop(request_id, None)

    async def disconnect_all(self, reason: str) -> None:
        async with self._registry_lock:
            states = list(self.connections.values())
            self.connections.clear()
            self._refresh_profile_snapshot_locked()
        for state in states:
            self._fail_pending(state, reason, "CANCELLED")
            try:
                await state.websocket.close(code=4000, reason=reason[:120])
            except Exception:
                pass
