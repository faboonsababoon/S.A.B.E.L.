"""Launch real SABEL plus two real WebSocket protocol clients.

Only Chrome itself is simulated.  The command loop, Ollama router, resolver,
state, dispatcher, WebSocket server, registry, and result verification are the
production implementations.
"""

import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.client import connect


ROOT = Path(__file__).resolve().parents[2]
INTERNAL_MARKERS = (
    "ToolCall(",
    "ResolvedRequest(",
    "open_application",
    "open_service",
    "search_web",
    "search_youtube",
    "delegate_to_openai",
    '"profile_id"',
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class ExtensionSimulator:
    def __init__(self, profile_id: str, profile_name: str, origin: str, tab_base: int):
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.origin = origin
        self.tab_base = tab_base
        self.tabs: dict[int, str] = {}
        self.commands: list[dict[str, object]] = []
        self.ready = asyncio.Event()
        self._task = None
        self._error = None

    def start(self, port: int, token: str) -> None:
        self._task = asyncio.create_task(self._run(port, token))

    async def wait_ready(self) -> None:
        await asyncio.wait_for(self.ready.wait(), 10)
        if self._error:
            raise self._error

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass

    async def _run(self, port: int, token: str) -> None:
        try:
            async with connect(f"ws://127.0.0.1:{port}/", origin=self.origin) as websocket:
                registration_id = f"register-{self.profile_id}"
                await websocket.send(
                    json.dumps(
                        {
                            "protocol_version": 1,
                            "type": "register",
                            "request_id": registration_id,
                            "token": token,
                            "profile_id": self.profile_id,
                            "profile_name": self.profile_name,
                            "extension_version": "acceptance-1",
                            "instance_id": f"simulator-{self.profile_id}",
                        }
                    )
                )
                registered = json.loads(await websocket.recv())
                if registered.get("type") != "registered" or registered.get("profile_id") != self.profile_id:
                    raise AssertionError(f"Registration failed: {registered}")
                self.ready.set()
                async for raw in websocket:
                    command = json.loads(raw)
                    if command.get("type") != "command":
                        continue
                    if command.get("profile_id") != self.profile_id:
                        raise AssertionError(
                            f"{self.profile_id} received another profile's command: {command}"
                        )
                    self.commands.append(command)
                    await websocket.send(json.dumps(self._response(command)))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._error = error
            self.ready.set()

    def _response(self, command: dict[str, object]) -> dict[str, object]:
        action = command["action"]
        arguments = dict(command["arguments"])
        if action == "browser_open_tab":
            tab_id = self.tab_base + len(self.tabs) + 1
            url = str(arguments["url"])
            self.tabs[tab_id] = url
            result = {"verified": True, "tab_id": tab_id, "url": url}
        elif action == "browser_navigate":
            tab_id = int(arguments["tab_id"])
            if tab_id not in self.tabs:
                return self._failure(command, "TAB_NOT_FOUND", "The simulated tab is absent.")
            url = str(arguments["url"])
            self.tabs[tab_id] = url
            result = {"verified": True, "tab_id": tab_id, "url": url}
        elif action == "browser_list_tabs":
            result = {
                "verified": True,
                "tabs": [
                    {"tab_id": tab_id, "title": f"Test {tab_id}", "url": url, "active": False}
                    for tab_id, url in self.tabs.items()
                ],
            }
        elif action == "browser_stop_task":
            result = {"verified": True, "stopped": True}
        else:
            return self._failure(command, "UNSUPPORTED_TEST_ACTION", str(action))
        return {
            "protocol_version": 1,
            "type": "response",
            "request_id": command["request_id"],
            "profile_id": self.profile_id,
            "success": True,
            "result": result,
            "error": None,
        }

    def _failure(self, command, code, message):
        return {
            "protocol_version": 1,
            "type": "response",
            "request_id": command["request_id"],
            "profile_id": self.profile_id,
            "success": False,
            "result": {},
            "error": {"code": code, "message": message},
        }

    def destinations(self) -> list[dict[str, object]]:
        destinations = []
        for command in self.commands:
            arguments = command["arguments"]
            url = arguments.get("url") if isinstance(arguments, dict) else None
            if not isinstance(url, str):
                continue
            parsed = urlsplit(url)
            query_values = parse_qs(parsed.query)
            if "youtube.com" in (parsed.hostname or ""):
                provider = "youtube"
                query = (query_values.get("search_query") or [None])[0]
            elif "google.com" in (parsed.hostname or ""):
                provider = "google"
                query = (query_values.get("q") or [None])[0]
            else:
                provider = parsed.hostname
                query = None
            destinations.append(
                {
                    "profile_id": self.profile_id,
                    "provider": provider,
                    "query": query,
                    "url": url,
                    "tab_id": arguments.get("tab_id"),
                }
            )
        return destinations


class SabelProcess:
    def __init__(self, process):
        self.process = process
        self.outputs = []

    async def command(self, text: str, timeout: float = 90.0) -> str:
        self.process.stdin.write((text + "\n").encode())
        await self.process.stdin.drain()
        raw = await asyncio.wait_for(
            self.process.stdout.readuntil(b"SABEL > "), timeout
        )
        output = raw.decode("utf-8", errors="replace").removesuffix("SABEL > ").strip()
        self.outputs.append((text, output))
        if "SABEL:" not in output:
            raise AssertionError(f"No visible SABEL response for {text!r}: {output!r}")
        for marker in INTERNAL_MARKERS:
            if marker in output:
                raise AssertionError(f"Internal value {marker!r} leaked in: {output}")
        return output

    async def exit(self) -> tuple[str, str, int]:
        self.process.stdin.write(b"exit\n")
        await self.process.stdin.drain()
        stdout = (await asyncio.wait_for(self.process.stdout.read(), 30)).decode(errors="replace")
        stderr = (await self.process.stderr.read()).decode(errors="replace")
        code = await self.process.wait()
        return stdout, stderr, code


async def _start_process(config: Path, port: int) -> SabelProcess:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "SABEL_CONFIG_DIR": str(config),
            "SABEL_BROWSER_BRIDGE_PORT": str(port),
            "SABEL_CLOUD_MODE": "off",
            "OLLAMA_MODEL": "qwen3:1.7b",
        }
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "main.py"),
        "--cloud",
        "off",
        cwd=str(ROOT),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        startup = await asyncio.wait_for(process.stdout.readuntil(b"SABEL > "), 20)
    except Exception:
        process.kill()
        stdout, stderr = await process.communicate()
        raise RuntimeError(
            f"SABEL did not start. stdout={stdout.decode(errors='replace')} "
            f"stderr={stderr.decode(errors='replace')}"
        )
    decoded = startup.decode(errors="replace")
    if f"Listening on 127.0.0.1:{port}" not in decoded:
        process.kill()
        raise RuntimeError(f"Browser bridge did not start: {decoded}")
    return SabelProcess(process)


async def run() -> int:
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="sabel-acceptance-") as directory:
        config = Path(directory)
        origins = ["chrome-extension://" + "a" * 32, "chrome-extension://" + "b" * 32]
        (config / "browser-origins.json").write_text(
            json.dumps(origins), encoding="utf-8"
        )
        sabel = await _start_process(config, port)
        token = (config / "browser-token").read_text(encoding="utf-8").strip()
        personal = ExtensionSimulator("personal", "Personal", origins[0], 1000)
        nyu = ExtensionSimulator("nyu", "NYU", origins[1], 2000)
        personal.start(port, token)
        nyu.start(port, token)
        try:
            await asyncio.gather(personal.wait_ready(), nyu.wait_ready())
            await asyncio.sleep(0.1)

            profile_output = await sabel.command("show browser profiles")
            assert "Personal (personal)" in profile_output and "NYU (nyu)" in profile_output

            commands = (
                "search black moss candles on my NYU profile on Google",
                "search black moss candles on YouTube on my NYU profile",
                "now search fried chickpeas on my Personal profile on Google",
                "now do the same on my NYU profile",
                "do the same",
                "go to YouTube on my NYU profile",
                "open Circles on YouTube on my NYU profile",
            )
            outputs = [await sabel.command(command) for command in commands]
            assert all("NYU Chrome profile" in output for output in outputs[:2])
            assert "Personal Chrome profile" in outputs[2]
            assert all("NYU Chrome profile" in output for output in outputs[3:])

            before_failure = len(personal.commands) + len(nyu.commands)
            failure = await sabel.command("its not working")
            assert "last action" in failure.casefold() and "verified" in failure.casefold(), failure
            assert len(personal.commands) + len(nyu.commands) == before_failure

            media_question = await sabel.command("play circles")
            assert "Spotify or YouTube" in media_question
            media_result = await sabel.command("youtube. do it on my NYU profile")
            assert "circles" in media_result.casefold() and "NYU Chrome profile" in media_result, media_result

            await sabel.command("search baseline on Google in my Personal profile")
            for command in (
                "go to YouTube on my NYU profile",
                "go to YouTube on my NYU profile",
                "wrong profile. Open YouTube on my NYU profile",
            ):
                output = await sabel.command(command)
                assert "NYU Chrome profile" in output

            personal_destinations = personal.destinations()
            nyu_destinations = nyu.destinations()
            assert len(personal_destinations) == 2, personal_destinations
            assert all(item["profile_id"] == "personal" for item in personal_destinations)
            assert all(item["profile_id"] == "nyu" for item in nyu_destinations)

            expected_first = [
                ("nyu", "google", "black moss candles"),
                ("nyu", "youtube", "black moss candles"),
                ("nyu", "google", "fried chickpeas"),
                ("nyu", "google", "fried chickpeas"),
                ("nyu", "youtube", None),
                ("nyu", "youtube", "Circles"),
                ("nyu", "youtube", "circles"),
            ]
            actual_first = [
                (item["profile_id"], item["provider"], item["query"])
                for item in nyu_destinations[:7]
            ]
            assert actual_first == expected_first, actual_first
            assert [
                (item["provider"], item["query"])
                for item in personal_destinations
            ] == [("google", "fried chickpeas"), ("google", "baseline")]
            for item in personal_destinations + nyu_destinations:
                query = item["query"]
                if query:
                    folded = str(query).casefold()
                    assert "profile" not in folded and " on google" not in folded and " on youtube" not in folded
            assert all((item["tab_id"] is None or int(item["tab_id"]) >= 2000) for item in nyu_destinations)
            assert all((item["tab_id"] is None or int(item["tab_id"]) < 2000) for item in personal_destinations)

            stdout, stderr, code = await sabel.exit()
            assert code == 0, (code, stdout, stderr)
            if "Traceback" in stderr:
                raise AssertionError(stderr)
            print("CLI_BLACK_BOX=PASS")
            print("DUAL_PROFILE_WEBSOCKET=PASS")
            print(f"Personal commands={len(personal.commands)} NYU commands={len(nyu.commands)}")
            print("No cross-profile dispatch, routing residue, raw tool leakage, or stale tab crossover detected.")
            return 0
        finally:
            if sabel.process.returncode is None:
                sabel.process.kill()
                await sabel.process.wait()
            await asyncio.gather(personal.stop(), nyu.stop())


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
