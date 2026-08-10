import unittest

from sabel.browser_models import BrowserSearchRequest, LastBrowserReference
from sabel.browser_routing import (
    build_search_url,
    parse_general_browser_task,
    parse_browser_search_request,
    validate_search_query,
    verify_search_url,
)


class BrowserSearchRoutingTests(unittest.TestCase):
    def test_general_task_routing_is_distinct_from_simple_open_and_search(self):
        self.assertIsNone(parse_general_browser_task("open YouTube"))
        self.assertIsNone(
            parse_general_browser_task("search green water bottles on Google")
        )
        youtube = parse_general_browser_task(
            "Go to YouTube and find Veritasium's newest video."
        )
        self.assertEqual(youtube.service_name, "youtube")
        self.assertEqual(youtube.profile_id, "personal")
        nyu = parse_general_browser_task(
            "Go to YouTube and find X on my NYU profile"
        )
        self.assertEqual(nyu.profile_id, "nyu")
        docs = parse_general_browser_task(
            "Go to https://docs.python.org/3/library/subprocess.html and find the subprocess.run section"
        )
        self.assertEqual(
            docs.initial_url,
            "https://docs.python.org/3/library/subprocess.html",
        )

    def test_unregistered_named_site_requires_exact_starting_url(self):
        parsed = parse_general_browser_task(
            "Search Best Buy for 2TB SSDs and tell me which visible option is cheapest"
        )
        self.assertTrue(parsed.missing_destination)
        self.assertIsNone(parsed.service_name)
        self.assertIsNone(parsed.initial_url)

    def reference(self, *, profile="nyu", provider="youtube", tab_id=17):
        return LastBrowserReference(
            profile_id=profile,
            service=provider,
            search_engine=provider,
            tab_id=tab_id,
            completed_at=100.0,
        )

    def test_required_transcript_routes_profile_provider_and_query_separately(self):
        cases = {
            'in the same profile, search up "matt rober"': (
                "nyu",
                "youtube",
                "matt rober",
            ),
            'in youtube search up Matt Rober" in my nyu profile': (
                "nyu",
                "youtube",
                "Matt Rober",
            ),
            "now go to google and search up matie stone in my nyu profile": (
                "nyu",
                "google",
                "matie stone",
            ),
            "search up matt rober in google in my nyu profile": (
                "nyu",
                "google",
                "matt rober",
            ),
            'search "green water bottles" in google in my personal profile': (
                "personal",
                "google",
                "green water bottles",
            ),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = parse_browser_search_request(
                    text, reference=self.reference()
                )
                self.assertEqual(
                    (parsed.profile_id, parsed.search_engine, parsed.query),
                    expected,
                )

    def test_explicit_provider_and_profile_override_old_context(self):
        parsed = parse_browser_search_request(
            "search green bottles in google in my personal profile",
            reference=self.reference(profile="nyu", provider="youtube"),
        )
        self.assertEqual(parsed.profile_id, "personal")
        self.assertEqual(parsed.search_engine, "google")
        self.assertEqual(parsed.query, "green bottles")

    def test_saved_default_is_used_only_when_current_turn_and_context_are_silent(self):
        defaulted = parse_browser_search_request(
            "search cats", default_search_engine="bing"
        )
        explicit = parse_browser_search_request(
            "search cats on YouTube", default_search_engine="bing"
        )
        self.assertEqual((defaulted.search_engine, defaulted.profile_id), ("bing", "personal"))
        self.assertEqual(explicit.search_engine, "youtube")

    def test_same_profile_and_there_use_verified_reference(self):
        same = parse_browser_search_request(
            'in the same profile search "cats"', reference=self.reference()
        )
        there = parse_browser_search_request(
            "search Matt Rober there", reference=self.reference()
        )
        self.assertEqual((same.profile_id, same.search_engine), ("nyu", "youtube"))
        self.assertEqual((there.profile_id, there.search_engine), ("nyu", "youtube"))

    def test_same_profile_without_context_is_not_guessed(self):
        parsed = parse_browser_search_request(
            'in the same profile search "cats"'
        )
        self.assertEqual(parsed.missing_context, "profile")
        self.assertIsNone(parsed.profile_id)
        self.assertIsNone(parsed.search_engine)

    def test_quoted_query_is_literal_and_routing_words_are_excluded(self):
        parsed = parse_browser_search_request(
            'search "green water bottles" in google in my personal profile'
        )
        self.assertEqual(parsed.query, "green water bottles")
        self.assertNotIn("google", parsed.query.casefold())
        self.assertNotIn("profile", parsed.query.casefold())

    def test_unmatched_outer_quote_is_recovered_without_deleting_punctuation(self):
        parsed = parse_browser_search_request(
            'in youtube search up Matt Rober" in my nyu profile'
        )
        punctuation = parse_browser_search_request(
            'search "C++: pointers & references?" in google in personal'
        )
        self.assertEqual(parsed.query, "Matt Rober")
        self.assertEqual(punctuation.query, "C++: pointers & references?")

    def test_same_string_uses_only_stored_literal_query(self):
        parsed = parse_browser_search_request(
            "search that same string up in google.com",
            last_query="how to bake cookies",
        )
        self.assertEqual(parsed.query, "how to bake cookies")
        self.assertEqual(parsed.search_engine, "google")

    def test_same_thing_ignores_discourse_connector_and_routing_residue(self):
        parsed = parse_browser_search_request(
            "now search the same thing on youtube but on personal profile",
            reference=self.reference(profile="nyu", provider="google"),
            last_query="green water bottles",
        )
        self.assertEqual(
            (parsed.query, parsed.search_engine, parsed.profile_id),
            ("green water bottles", "youtube", "personal"),
        )

    def test_literal_spelling_is_never_corrected(self):
        for query in ("matie stone", "Matt Rober"):
            parsed = parse_browser_search_request(
                f"search {query} in google in my nyu profile"
            )
            self.assertEqual(parsed.query, query)

    def test_search_for_framing_is_not_part_of_the_query(self):
        parsed = parse_browser_search_request(
            "search for cats on Google",
        )
        self.assertEqual(parsed.search_engine, "google")
        self.assertEqual(parsed.query, "cats")

    def test_provider_led_search_shorthand_and_demonstrated_typo(self):
        cases = {
            "ggoogle nintendo 3ds using my personal profile": (
                "google",
                "personal",
                "nintendo 3ds",
            ),
            'google "nintendo 3ds" on my personal profile': (
                "google",
                "personal",
                "nintendo 3ds",
            ),
            "google nintendo 3ds": ("google", "personal", "nintendo 3ds"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = parse_browser_search_request(text)
                self.assertEqual(
                    (parsed.search_engine, parsed.profile_id, parsed.query),
                    expected,
                )

    def test_google_chrome_shorthand_remains_an_application_request(self):
        self.assertIsNone(parse_browser_search_request("google chrome"))

    def test_provider_led_conflict_is_not_executed(self):
        parsed = parse_browser_search_request("google cats on youtube")
        self.assertEqual(parsed.routing_conflict, "search_engine")
        self.assertIsNone(parsed.search_engine)

    def test_google_chrome_is_not_treated_as_a_provider(self):
        self.assertIsNone(parse_browser_search_request("open Google Chrome"))
        parsed = parse_browser_search_request(
            "search Google Chrome keyboard shortcuts in my personal profile"
        )
        self.assertIsNone(parsed.search_engine)
        self.assertEqual(parsed.query, "Google Chrome keyboard shortcuts")

    def test_query_validation_rejects_empty_length_and_routing_residue(self):
        for value in (
            "",
            "cats in my nyu profile",
            "cats in google",
            "search up cats",
            "cats in the same profile",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_search_query(value)
        with self.assertRaises(ValueError):
            validate_search_query("x" * 501)

    def test_url_generation_and_verification_are_provider_specific(self):
        google = build_search_url("google", "green water bottles")
        youtube = build_search_url("youtube", "Matt Rober")
        self.assertEqual(
            google, "https://www.google.com/search?q=green+water+bottles"
        )
        self.assertEqual(
            youtube,
            "https://www.youtube.com/results?search_query=Matt+Rober",
        )
        self.assertTrue(verify_search_url(google, "google", "green water bottles"))
        self.assertTrue(verify_search_url(youtube, "youtube", "Matt Rober"))
        self.assertFalse(verify_search_url(youtube, "google", "Matt Rober"))
        self.assertFalse(verify_search_url(google, "google", "old query"))

    def test_search_request_is_frozen_and_keeps_target_tab_separate(self):
        request = BrowserSearchRequest("personal", "google", "cats", 4)
        self.assertEqual(request.target_tab_id, 4)
        with self.assertRaises((AttributeError, TypeError)):
            request.query = "dogs"


if __name__ == "__main__":
    unittest.main()
