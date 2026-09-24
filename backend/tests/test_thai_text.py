from __future__ import annotations

import unittest

from thailex_api.thai_text import context_tokens, has_context, normalize_typing, segment


def texts(query: str, words: set[str]) -> list[str]:
    return [token.text for token in segment(query, words)]


class ThaiSegmentationTests(unittest.TestCase):
    def test_known_words_split_unknown_neighbours(self) -> None:
        self.assertEqual(texts("พ่อขันนอตให้แน่น", {"ขัน"}), ["พ่อ", "ขัน", "นอต", "ให้", "แน่น"])
        self.assertEqual(texts("ดาวเทียมโคจรรอบโลก", {"ดาว", "ดาวเทียม"})[0], "ดาวเทียม")

    def test_short_function_word_does_not_break_unknown_word(self) -> None:
        self.assertIn("สว่าง", texts("คืนนี้ดาวสว่างมาก", {"ดาว"}))

    def test_question_phrasing_is_not_context(self) -> None:
        for query in (
            "ดาว",
            "ดาว มีความหมายอะไรบ้าง",
            "คำว่า ดาว หมายถึงอะไร",
            "ผมอยากรู้ว่า คำว่าดาวอะมีความหมายว่าอะไรได้บ้างมีรายละเอียดยังไง",
        ):
            with self.subTest(query=query):
                self.assertFalse(has_context(query, "ดาว"))

    def test_dictionary_phrase_made_of_question_words_is_not_context(self) -> None:
        lexicon = {"ดาว", "กระดูก", "มีความหมาย", "คืนนี้"}
        self.assertFalse(has_context("คำว่ากระดูกมีความหมายว่าอะไรได้บ้าง", "กระดูก", lexicon))
        self.assertFalse(has_context("ดาว มีความหมายอะไรบ้าง", "ดาว", lexicon))
        self.assertTrue(has_context("คืนนี้ดาวสว่างมาก", "ดาว", lexicon))

    def test_typing_slips_are_normalized(self) -> None:
        self.assertEqual(normalize_typing("คำเเบบไหน​"), "คำแบบไหน")

    def test_word_property_questions_are_not_context(self) -> None:
        self.assertFalse(has_context("รากศัพท์ของคำว่าไก่คือ", "ไก่", {"ไก่"}))
        self.assertFalse(has_context("อ้วน เป็นลักษณะคำแบบไหน", "อ้วน", {"อ้วน"}))

    def test_sentence_around_word_is_context(self) -> None:
        self.assertTrue(has_context("คืนนี้ดาวสว่างมาก", "ดาว"))
        self.assertTrue(has_context("เขาเป็นดาวของห้อง", "ดาว"))
        weights = dict(context_tokens("เขาเป็นดาวของห้อง", "ดาว"))
        self.assertEqual(weights["ห้อง"], 1.0)
        self.assertEqual(weights["เป็น"], 0.25)


if __name__ == "__main__":
    unittest.main()
