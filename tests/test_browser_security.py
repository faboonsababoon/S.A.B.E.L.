import os
from pathlib import Path
import stat
import tempfile
import unittest

from sabel.browser_security import (
    BrowserSecurityError,
    BrowserTokenStore,
    ExtensionOriginStore,
    SlidingWindowRateLimiter,
    domain_in_scope,
    restricted_domain,
    validate_http_url,
)


class BrowserSecurityTests(unittest.TestCase):
    def test_token_is_created_once_with_restrictive_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".sabel" / "browser-token"
            store = BrowserTokenStore(path, token_factory=lambda size: "fixed-secret")
            first = store.load_or_create()
            second = store.load_or_create()
            self.assertEqual(first, second)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_token_comparison_and_rotation(self):
        values = iter(["first-secret", "second-secret"])
        with tempfile.TemporaryDirectory() as directory:
            store = BrowserTokenStore(
                Path(directory) / "browser-token",
                token_factory=lambda size: next(values),
            )
            self.assertEqual(store.load_or_create(), "first-secret")
            self.assertTrue(store.matches("first-secret", "first-secret"))
            self.assertFalse(store.matches("first-secret", "wrong"))
            self.assertEqual(store.rotate(), "second-secret")
            self.assertEqual(store.load_or_create(), "second-secret")

    def test_symlink_token_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("secret", encoding="utf-8")
            link = root / "browser-token"
            os.symlink(target, link)
            with self.assertRaisesRegex(BrowserSecurityError, "regular file"):
                BrowserTokenStore(link).load_or_create()

    def test_extension_origins_are_explicitly_registered(self):
        extension_id = "a" * 32
        with tempfile.TemporaryDirectory() as directory:
            store = ExtensionOriginStore(Path(directory) / "origins.json")
            origin = store.register_extension_id(extension_id)
            self.assertTrue(store.is_allowed(origin))
            self.assertFalse(store.is_allowed("https://example.com"))
            self.assertFalse(store.is_allowed(None))
            with self.assertRaises(BrowserSecurityError):
                store.register_extension_id("not-an-extension-id")

    def test_symlink_origin_allowlist_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("[]", encoding="utf-8")
            linked = root / "origins.json"
            linked.symlink_to(target)
            with self.assertRaisesRegex(BrowserSecurityError, "regular file"):
                ExtensionOriginStore(linked).allowed_origins()

    def test_navigation_and_domain_scope(self):
        self.assertEqual(validate_http_url("https://www.youtube.com/"), "https://www.youtube.com/")
        for unsafe in ("file:///tmp/a", "javascript:alert(1)", "chrome://settings", "data:text/plain,x"):
            with self.subTest(url=unsafe):
                with self.assertRaises(BrowserSecurityError):
                    validate_http_url(unsafe)
        self.assertTrue(domain_in_scope("studio.youtube.com", {"youtube.com"}))
        self.assertFalse(domain_in_scope("youtube.example.com", {"youtube.com"}))
        self.assertTrue(restricted_domain("secure.chase.com"))
        self.assertTrue(restricted_domain("patient.example.edu"))
        self.assertFalse(restricted_domain("docs.python.org"))

    def test_rate_limit_is_bounded(self):
        limiter = SlidingWindowRateLimiter(2, 10)
        self.assertTrue(limiter.allow(now=0))
        self.assertTrue(limiter.allow(now=1))
        self.assertFalse(limiter.allow(now=2))
        self.assertTrue(limiter.allow(now=11))


if __name__ == "__main__":
    unittest.main()
