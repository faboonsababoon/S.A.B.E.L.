import unittest

from sabel.confirmations import (
    is_explicit_trash_confirmation,
    normalize_confirmation_arguments,
)


class ConfirmationTests(unittest.TestCase):
    def test_clear_contextual_confirmations(self):
        for text in (
            "Yes, empty the Trash",
            "Yes, do it",
            "Go ahead",
            "Proceed",
            "Okay, proceed",
            "I confirm",
            "Permanently delete it",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_explicit_trash_confirmation(text))

    def test_uncertain_or_negative_text_never_confirms(self):
        for text in ("okay", "maybe", "sure", "No", "Do not delete it", "Never mind"):
            with self.subTest(text=text):
                self.assertFalse(is_explicit_trash_confirmation(text))

    def test_only_matching_redundant_identity_fields_are_normalized(self):
        self.assertEqual(normalize_confirmation_arguments({}, "empty_trash"), {})
        self.assertEqual(
            normalize_confirmation_arguments({"action": "empty_trash"}, "empty_trash"),
            {},
        )
        self.assertIsNone(
            normalize_confirmation_arguments({"action": "open_website"}, "empty_trash")
        )
        self.assertIsNone(
            normalize_confirmation_arguments({"force": True}, "empty_trash")
        )


if __name__ == "__main__":
    unittest.main()
