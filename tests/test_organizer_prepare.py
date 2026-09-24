import unittest

from thailex_ingestion.organizer_prepare import (
    dialect_paragraphs,
    numbered_segments,
    royal_2542,
    royal_2554,
    split_leading,
    technical_terms,
)

# All rows below are synthetic fixtures shaped like the organizer layouts.


class OrganizerPrepareTests(unittest.TestCase):
    def test_leading_labels_pronunciation_and_pos_are_separated(self) -> None:
        leading = split_leading("[โบ อ่านว่า กอข้อ] (ปาก) น. คำทดสอบ.")
        self.assertEqual(leading.pronunciations, ["กอข้อ"])
        self.assertEqual(leading.labels, ["(โบ)", "(ปาก)"])
        self.assertEqual(leading.pos, "น.")
        self.assertEqual(leading.remainder, "คำทดสอบ.")

    def test_pos_like_text_inside_definition_is_not_taken(self) -> None:
        leading = split_leading("ดู กระดูกอึ่ง.")
        self.assertEqual(leading.pos, "")
        self.assertEqual(leading.remainder, "ดู กระดูกอึ่ง.")

    def test_royal_2542_uses_row_position_homograph_and_variants(self) -> None:
        header = ["number", "K_W", "M_W", "D_T"]
        rows = [
            [None, "ทดสอบ,ทดสอบ ๒", "ทดสอบ ๒", "น. <i>ความหมาย</i>&#160;ทดสอบ."],
            [None, "นย,นย-,นยะ", "นย-, นยะ", "[นะยะ-] น. เค้าความ."],
        ]
        prepared = list(royal_2542(header, rows))
        self.assertEqual(prepared[0]["id"], "2542-00001")
        self.assertEqual((prepared[0]["lemma"], prepared[0]["sense_number"]), ("ทดสอบ", "๒"))
        self.assertEqual(prepared[0]["definition"], "ความหมาย ทดสอบ.")
        self.assertEqual([row["lemma"] for row in prepared[1:]], ["นย", "นยะ"])
        self.assertEqual(prepared[1]["pronunciations"], "นะยะ-")

    def test_royal_2554_sub_senses_inherit_pos_and_keep_labelled_notes(self) -> None:
        header = [str(index) for index in range(23)]
        first = ["7", "คำ", "๑", "อ่าน", "null", "น.", "นิยามแรก", "null", "0"] + ["null"] * 14
        second = ["7", "คำ", "๑", "null", "null", "null", "นิยามสอง", "null", "0"] + ["null"] * 14
        second[12] = "ปาก"
        prepared = list(royal_2554(header, [first, second]))
        self.assertEqual([row["id"] for row in prepared], ["2554-7-1", "2554-7-2"])
        self.assertEqual(prepared[1]["pos"], "น.")
        self.assertEqual(prepared[0]["pronunciations"], "อ่าน")
        self.assertEqual(prepared[1]["notes"], "ระดับภาษา: ปาก")

    def test_numbered_segments(self) -> None:
        self.assertEqual(numbered_segments("๑. สัต ๒. ภาวะ"), [("1", "สัต"), ("2", "ภาวะ")])
        self.assertEqual(numbered_segments("ไม่มีเลข"), [("", "ไม่มีเลข")])

    def test_technical_terms_split_alternatives_and_keep_english_translation(self) -> None:
        header = ["number", "en", "th", "field", "date", "other", "ref", "x", "desc"]
        rows = [
            ["1", "term", "น. คำหนึ่ง, คำสอง [มีความหมายเหมือนกับ x]", "สาขา", "2004-05-01 00:00:00", "", "", "", ""],
            ["2", None, "2. คำสาม", "สาขา", "", "", "", "", ""],
        ]
        prepared = list(technical_terms(header, rows))
        self.assertEqual([row["lemma"] for row in prepared], ["คำหนึ่ง", "คำสอง", "คำสาม"])
        self.assertEqual(prepared[0]["pos"], "น.")
        self.assertIn("[มีความหมายเหมือนกับ x]", prepared[0]["notes"])
        self.assertEqual(prepared[2]["translations"], "term")

    def test_dialect_paragraph_reads_brackets_as_pronunciations(self) -> None:
        paragraph = "คำถิ่น\tสคริปต์\t[คำ-ถิ่น]\t[kham1]\nน. ความหมายถิ่น."
        prepared = list(dialect_paragraphs(["หัวข้อ", paragraph], "dialect-test"))
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["lemma"], "คำถิ่น")
        self.assertEqual(prepared[0]["pronunciations"], "คำ-ถิ่น|kham1")
        self.assertEqual((prepared[0]["pos"], prepared[0]["definition"]), ("น.", "ความหมายถิ่น."))


if __name__ == "__main__":
    unittest.main()
