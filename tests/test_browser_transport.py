import asyncio
import json
import logging
from pathlib import Path
import tempfile
import unittest

from sabel.browser_models import BrowserProfile
from sabel.browser_protocol import ProtocolError
from sabel.browser_security import BrowserTokenStore, ExtensionOriginStore, SlidingWindowRateLimiter
from sabel.browser_transport import WebSocketBrowserTransport, _ConnectionState


def registration(token="bridge-secret", **changes):
    value = {
        "protocol_version": 1,
        "type": "register",
        "request_id": "register-1",
        "token": token,
        "profile_id": "personal",
        "profile_name": "Personal",
        "extension_version": "0.1.0",
        "instance_id": "instance-1",
    }
    value.update(changes)
    return json.dumps(value)


class FakeWebSocket:
    def __init__(self, incoming=None, wait_after=False):
        self.incoming = list(incoming or [])
        self.sent = []
        self.closed = False
        self.close_code = None
        self.wait_after = wait_after
        self.release = asyncio.Event()

    async def recv(self):
        if self.incoming:
            return self.incoming.pop(0)
        await self.release.wait()
        raise RuntimeError("released")

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.incoming:
            return self.incoming.pop(0)
        if self.wait_after and not self.closed:
            await self.release.wait()
        raise StopAsyncIteration

    async def send(self, value):
        self.sent.append(value)

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code
        self.release.set()


class FakeServer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class FakeHandshakeConnection:
    def respond(self, status, body):
        return status, body


class FakeRequest:
    def __init__(self, origin):
        self.headers = {"Origin": origin} if origin is not None else {}


class BrowserTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.token_store = BrowserTokenStore(
            root / "browser-token", token_factory=lambda size: "bridge-secret"
        )
        self.origin_store = ExtensionOriginStore(root / "origins.json")
        self.origin = self.origin_store.register_extension_id("a" * 32)

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def transport(self, **values):
        return WebSocketBrowserTransport(
            port=8765,
            token_store=self.token_store,
            origin_store=self.origin_store,
            **values,
        )

    async def test_server_binds_only_to_loopback_with_resource_limits(self):
        captured = {}

        async def fake_serve(handler, host, port, **options):
            captured.update(host=host, port=port, options=options)
            return FakeServer()

        transport = self.transport(serve_factory=fake_serve)
        await transport.start()
        self.assertEqual(captured["host"], "127.0.0.1")
        self.assertEqual(captured["port"], 8765)
        self.assertEqual(captured["options"]["max_size"], 262_144)
        self.assertEqual(captured["options"]["max_queue"], 32)
        await transport.stop()

    async def test_origin_validation_rejects_pages_and_accepts_registered_extension(self):
        transport = self.transport()
        accepted = await transport._process_request(
            FakeHandshakeConnection(), FakeRequest(self.origin)
        )
        rejected = await transport._process_request(
            FakeHandshakeConnection(), FakeRequest("https://example.com")
        )
        self.assertIsNone(accepted)
        self.assertEqual(int(rejected[0]), 403)

    async def test_correct_token_authenticates_and_wrong_or_missing_token_fails(self):
        transport = self.transport()
        transport._token = self.token_store.load_or_create()
        good = FakeWebSocket([registration()])
        await transport._handle_connection(good)
        self.assertEqual(json.loads(good.sent[0])["type"], "registered")

        bad = FakeWebSocket([registration("wrong-secret")])
        await transport._handle_connection(bad)
        self.assertEqual(bad.close_code, 4401)
        self.assertFalse(bad.sent)

        missing_value = json.loads(registration())
        missing_value.pop("token")
        missing = FakeWebSocket([json.dumps(missing_value)])
        await transport._handle_connection(missing)
        self.assertEqual(missing.close_code, 4400)

    async def test_registration_timeout_and_oversized_message_close_connection(self):
        transport = self.transport(registration_timeout=0.01, max_message_size=100)
        transport._token = self.token_store.load_or_create()
        timed_out = FakeWebSocket()
        await transport._handle_connection(timed_out)
        self.assertEqual(timed_out.close_code, 4408)

        oversized = FakeWebSocket(["x" * 101])
        await transport._handle_connection(oversized)
        self.assertEqual(oversized.close_code, 4400)

    async def test_disconnected_profile_is_removed_and_pending_requests_fail(self):
        transport = self.transport()
        websocket = FakeWebSocket(wait_after=True)
        profile = BrowserProfile("personal", "Personal", "instance-1", "0.1.0")
        state = _ConnectionState(
            websocket,
            profile,
            SlidingWindowRateLimiter(30, 10),
            semaphore=asyncio.Semaphore(4),
        )
        future = asyncio.get_running_loop().create_future()
        state.pending["request-1"] = future
        await transport._register_connection(state)
        self.assertEqual(len(transport.connected_profiles()), 1)
        await transport._remove_connection(state)
        self.assertFalse(transport.connected_profiles())
        self.assertEqual((await future).error_code, "PROFILE_DISCONNECTED")

    async def test_disconnected_profile_and_command_timeout_are_readable(self):
        transport = self.transport(command_timeout=0.01)
        missing = await transport.send_request("nyu", "browser_list_tabs", {})
        self.assertIn("NYU", missing.error)

        websocket = FakeWebSocket(wait_after=True)
        state = _ConnectionState(
            websocket,
            BrowserProfile("personal", "Personal", "instance-1", "0.1.0"),
            SlidingWindowRateLimiter(30, 10),
            semaphore=asyncio.Semaphore(4),
        )
        transport._connections["personal"] = state
        timed_out = await transport.send_request("personal", "browser_list_tabs", {})
        self.assertEqual(timed_out.error_code, "COMMAND_TIMEOUT")

    async def test_duplicate_response_ids_and_connection_rate_limit_fail_closed(self):
        transport = self.transport()
        websocket = FakeWebSocket()
        state = _ConnectionState(
            websocket,
            BrowserProfile("personal", "Personal", "instance-1", "0.1.0"),
            SlidingWindowRateLimiter(1, 10),
        )
        future = asyncio.get_running_loop().create_future()
        state.pending["request-1"] = future
        response = {
            "protocol_version": 1,
            "type": "response",
            "request_id": "request-1",
            "profile_id": "personal",
            "success": True,
            "result": {},
            "error": None,
        }
        await transport._handle_authenticated_message(state, response)
        with self.assertRaisesRegex(ProtocolError, "duplicate request ID"):
            await transport._handle_authenticated_message(state, response)

        transport._token = self.token_store.load_or_create()
        limited = FakeWebSocket(
            [
                registration(),
                json.dumps({
                    "protocol_version": 1,
                    "type": "heartbeat",
                    "request_id": "heartbeat-1",
                    "profile_id": "personal",
                }),
                json.dumps({
                    "protocol_version": 1,
                    "type": "heartbeat",
                    "request_id": "heartbeat-2",
                    "profile_id": "personal",
                }),
            ]
        )
        transport.rate_limit_count = 1
        await transport._handle_connection(limited)
        self.assertEqual(limited.close_code, 4429)

    async def test_token_rotation_disconnects_clients_and_never_logs_secret(self):
        stream = []

        class Capture(logging.Handler):
            def emit(self, record):
                stream.append(self.format(record))

        logger = logging.getLogger(f"sabel-test-{id(self)}")
        logger.setLevel(logging.INFO)
        logger.addHandler(Capture())
        values = iter(["bridge-secret", "rotated-secret"])
        root = Path(self.temporary.name)
        store = BrowserTokenStore(root / "rotate-token", token_factory=lambda size: next(values))
        transport = WebSocketBrowserTransport(
            port=8765,
            token_store=store,
            origin_store=self.origin_store,
            logger=logger,
        )
        transport._token = store.load_or_create()
        websocket = FakeWebSocket(wait_after=True)
        state = _ConnectionState(
            websocket,
            BrowserProfile("personal", "Personal", "instance-1", "0.1.0"),
            SlidingWindowRateLimiter(30, 10),
        )
        transport._connections["personal"] = state
        await transport.rotate_token()
        self.assertTrue(websocket.closed)
        self.assertFalse(transport.connected_profiles())
        logs = "\n".join(stream)
        self.assertNotIn("bridge-secret", logs)
        self.assertNotIn("rotated-secret", logs)


if __name__ == "__main__":
    unittest.main()
