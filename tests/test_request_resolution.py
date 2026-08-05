import plistlib
from pathlib import Path
import tempfile
import unittest

from sabel.actions import ActionResult
from sabel.application_catalog import ApplicationCatalog
from sabel.browser_models import BrowserActionContext
from sabel.conversation_state import ConversationState
from sabel.config import Settings
from sabel.request_models import Intent, ResolvedRequest
from sabel.request_resolution import RequestResolver, extract_locked_constraints
from sabel.tool_dispatcher import ToolDispatcher


def _make_app(root: Path, basename: str, display: str, identifier: str) -> None:
    bundle = root / f"{basename}.app" / "Contents"
    bundle.mkdir(parents=True)
    with (bundle / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleName": display,
                "CFBundleDisplayName": display,
                "CFBundleIdentifier": identifier,
            },
            stream,
        )


class RequestResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        _make_app(root, "Roblox", "Roblox", "example.roblox")
        _make_app(root, "RobloxStudio", "Roblox Studio", "example.roblox.studio")
        _make_app(root, "Codex", "Codex", "example.codex")
        self.catalog = ApplicationCatalog((root,), refresh_seconds=999)
        self.state = ConversationState(20)
        self.resolver = RequestResolver(self.state, self.catalog)

    def tearDown(self):
        self.temporary.cleanup()

    def resolve(self, text):
        return self.resolver.resolve(text, locked=extract_locked_constraints(text))

    def test_catalog_preserves_more_specific_application(self):
        match = self.catalog.resolve("roblox studios")
        self.assertEqual(match.application.display_name, "Roblox Studio")
        regular = next(
            candidate for candidate in match.candidates if candidate.display_name == "Roblox"
        )
        self.assertFalse(regular.accepted)
        self.assertIn('missing requested token "studio"', regular.reason)

    def test_shorter_application_is_never_a_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _make_app(root, "Roblox", "Roblox", "example.roblox")
            catalog = ApplicationCatalog((root,), refresh_seconds=999)
            match = catalog.resolve("Roblox Studios")
        self.assertIsNone(match.application)
        self.assertIn("I did not open Roblox", match.message)

    def test_current_turn_constraints_are_locked(self):
        locked = extract_locked_constraints(
            'wrong profile; search "green water bottles" on YouTube in my school profile'
        )
        self.assertEqual(locked.profile_id, "nyu")
        self.assertEqual(locked.search_engine, "youtube")
        self.assertEqual(locked.quoted_text, "green water bottles")
        self.assertTrue(locked.correction)

    def test_application_intent_never_inherits_browser_profile(self):
        self.state.record_browser_context("youtube", "nyu")
        decision = self.resolve("open Codex")
        self.assertEqual(decision.request.intent, Intent.OPEN_APPLICATION)
        self.assertEqual(decision.request.application_name, "Codex")
        self.assertIsNone(decision.request.profile_id)

    def test_installed_codex_dispatches_as_application_in_dry_run(self):
        opened = []

        def dry_open(name):
            opened.append(name)
            return ActionResult(True, "dry run")

        dispatcher = ToolDispatcher(
            Settings(),
            self.state,
            lambda: True,
            handlers={"open_application": dry_open},
            application_catalog=self.catalog,
        )
        result = dispatcher.dispatch(
            "open_application", {"application_name": "codex"}, "open codex"
        )
        self.assertTrue(result.action_success)
        self.assertEqual(opened, ["Codex"])
        self.assertEqual(result.message, "Opening Codex.")

    def test_search_paraphrases_resolve_identically(self):
        phrases = (
            "search cats on Google in my NYU profile",
            "in my NYU profile, search Google for cats",
            "Google cats using my NYU profile",
            "on NYU, look up cats with Google",
        )
        resolved = [self.resolve(text).request for text in phrases]
        self.assertEqual(
            {(item.intent, item.query, item.search_engine, item.profile_id) for item in resolved},
            {(Intent.SEARCH_WEB, "cats", "google", "nyu")},
        )

    def test_youtube_homepage_paraphrases_keep_explicit_nyu(self):
        phrases = (
            "open YouTube on my NYU profile",
            "on my NYU profile open YouTube",
            "using NYU, go to YouTube",
        )
        resolved = [self.resolve(text).request for text in phrases]
        self.assertEqual(
            {(item.intent, item.service, item.profile_id) for item in resolved},
            {(Intent.OPEN_SERVICE, "youtube", "nyu")},
        )

    def test_named_youtube_content_is_search_not_homepage(self):
        request = self.resolve("open Circles on YouTube on my NYU profile").request
        self.assertEqual(request.intent, Intent.SEARCH_YOUTUBE)
        self.assertEqual(request.query, "Circles")
        self.assertEqual(request.profile_id, "nyu")

    def test_do_same_clones_only_verified_action(self):
        original = ResolvedRequest(
            Intent.SEARCH_WEB,
            service="google",
            search_engine="google",
            query="fried chickpeas",
            profile_id="personal",
        )
        self.state.begin_action(original)
        self.state.complete_action(
            original,
            success=True,
            verified=True,
            message="verified",
            browser_context=BrowserActionContext(
                "personal", "google", "google", "fried chickpeas", 10,
                "https://www.google.com/search?q=fried+chickpeas",
            ),
        )
        nyu = self.resolve("now do the same on my NYU profile").request
        self.assertEqual((nyu.query, nyu.search_engine, nyu.profile_id), ("fried chickpeas", "google", "nyu"))
        self.state.begin_action(nyu)
        self.state.complete_action(nyu, success=True, verified=True, message="verified")
        repeated = self.resolve("do the same").request
        self.assertEqual((repeated.query, repeated.search_engine, repeated.profile_id), ("fried chickpeas", "google", "nyu"))

    def test_failed_action_never_becomes_reusable(self):
        request = ResolvedRequest(
            Intent.SEARCH_WEB,
            search_engine="google",
            query="failed query",
            profile_id="personal",
        )
        self.state.begin_action(request)
        self.state.complete_action(
            request, success=True, verified=False, message="not verified"
        )
        self.assertIsNone(self.state.reusable_action)
        self.assertIn("did not verify", self.state.contextual_failure_message())

    def test_user_rejected_success_is_not_reused(self):
        request = ResolvedRequest(
            Intent.OPEN_SERVICE, service="youtube", profile_id="personal"
        )
        self.state.begin_action(request)
        self.state.complete_action(
            request, success=True, verified=True, message="verified"
        )
        self.assertIsNotNone(self.state.reusable_action)
        self.state.reject_last_attempt()
        self.assertIsNone(self.state.reusable_action)
        decision = self.resolve("do the same")
        self.assertIsNone(decision.request)
        self.assertIn("do not have", decision.message)

    def test_model_cannot_override_explicit_profile_or_provider(self):
        text = "search black moss candles on YouTube in my NYU profile"
        decision = self.resolver.resolve(
            text,
            locked=extract_locked_constraints(text),
            model_tool_name="search_web",
            model_arguments={
                "query": "black moss candles on YouTube in my NYU profile",
                "search_engine": "google",
                "profile_id": "personal",
            },
        )
        self.assertEqual(decision.tool_name, "search_youtube")
        self.assertEqual(decision.arguments, {"query": "black moss candles", "profile_id": "nyu"})


if __name__ == "__main__":
    unittest.main()
