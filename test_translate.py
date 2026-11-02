import unittest

from translate import _read_chrome, _read_gtx, to_russian


class TranslateTest(unittest.TestCase):
    def test_russian_and_empty_stay(self):
        calls = []
        self.assertEqual(to_russian(""), "")
        self.assertEqual(to_russian("   "), "")
        self.assertEqual(
            to_russian("Уже по-русски [Issue#329]", block=lambda text: calls.append(text) or text),
            "Уже по-русски [Issue#329]",
        )
        self.assertEqual(calls, [])

    def test_paragraphs_and_markers(self):
        def block(text):
            if "delivers" in text:
                self.assertNotIn("[Issue#329]", text)
                self.assertIn("ZXQ0ZQ", text)
            return text.replace("delivers", "приносит").replace("Second", "Второе")

        self.assertEqual(
            to_russian("Studio delivers [Issue#329].\n\nSecond line.", block=block),
            "Studio приносит [Issue#329].\n\nВторое line.",
        )

    def test_readers_take_the_translated_sentence(self):
        self.assertEqual(
            _read_gtx([[["Древнее зло", "Ancient evil", None]]]),
            "Древнее зло",
        )
        self.assertEqual(_read_chrome(["Древнее зло"]), "Древнее зло")
        self.assertEqual(_read_chrome([["Древнее зло", "en"]]), "Древнее зло")

    def test_empty_translation_is_an_error(self):
        with self.assertRaises(RuntimeError):
            to_russian("Hello", block=lambda text: "  ")


if __name__ == "__main__":
    unittest.main()
