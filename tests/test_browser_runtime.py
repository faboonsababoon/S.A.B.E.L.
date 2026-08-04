import tempfile
from pathlib import Path
import unittest

from sabel.browser_models import BrowserProfile, BrowserResult
from sabel.browser_runtime import BrowserBridgeRuntime, _youtube_channel_target
from sabel.config import Settings


class FakeTransport:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.rotated = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def rotate_token(self):
        self.rotated = True

    async def send_request(self, profile_id, action, arguments):
        return BrowserResult.failed("not used", "NOT_USED")

    def connected_profiles(self):
        return [BrowserProfile("personal", "Personal", "instance", "0.1.0")]


class BrowserRuntimeTests(unittest.TestCase):
    def settings(self, directory):
        base = Path(directory)
        return Settings(
            browser_token_path=base / "token",
            browser_origin_path=base / "origins.json",
            browser_audit_path=base / "audit.json",
        )

    def test_background_runtime_starts_stops_and_rotates_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport()
            runtime = BrowserBridgeRuntime(self.settings(directory), transport)
            self.assertEqual(runtime.copilot.task_manager.max_steps, 8)
            runtime.start()
            self.assertTrue(transport.started)
            self.assertIn("Personal", runtime.show_profiles().message)
            runtime.rotate_token()
            self.assertTrue(transport.rotated)
            runtime.stop()
            self.assertTrue(transport.stopped)

    def test_youtube_target_extraction_is_bounded_to_channel_objective(self):
        self.assertEqual(
            _youtube_channel_target("Go to Taz Skylar's YouTube channel"),
            "Taz Skylar",
        )
        self.assertIsNone(_youtube_channel_target("Open a YouTube video"))


if __name__ == "__main__":
    unittest.main()
