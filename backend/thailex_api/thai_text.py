"""Small dictionary-based Thai word segmentation for query analysis.

Thai is written without spaces, so finding the target word and the context
around it needs segmentation. This module keeps the dependency footprint at
zero: it combines lemmas from the graph with a short built-in list of
function and question words, and uses maximal matching (fewest unknown
characters, then fewest tokens).
"""

from __future__ import annotations

import re
from collections.abc import Container
from dataclasses import dataclass


# Grammatical words. They segment sentences and carry a weak context signal,
# but are never chosen as the word the user is asking about.
FUNCTION_WORDS = frozenset({
    "เป็น", "คือ", "ของ", "ใน", "ที่", "และ", "หรือ", "กับ", "ได้", "ให้", "มี", "ไม่",
    "จะ", "ก็", "แล้ว", "นี้", "นั้น", "โน้น", "ว่า", "การ", "ความ", "ซึ่ง", "อัน",
    "ต่อ", "จาก", "ถึง", "แก่", "โดย", "เพื่อ", "เพราะ", "แต่", "ถ้า", "หาก", "ยัง",
    "อยู่", "ไป", "มา", "ถูก", "เคย", "กำลัง", "ต้อง", "อาจ", "คง", "จึง", "เลย",
    "ด้วย", "อีก", "ทุก", "บาง", "หลาย", "มาก", "น้อย", "กว่า", "ที่สุด", "เขา",
    "เธอ", "ผม", "ฉัน", "ดิฉัน", "หนู", "เรา", "คุณ", "มัน", "พวก", "ท่าน", "เอง",
    "กัน", "แบบ", "อย่าง", "เช่น", "ตอน", "เมื่อ", "ขณะ", "ครั้ง", "จริง", "ๆ",
})

# Words that shape a dictionary question rather than describe a situation.
# They are removed before judging whether the user supplied context.
QUESTION_WORDS = frozenset({
    "คำว่า", "คำนี้", "คำนั้น", "คำ", "ความหมาย", "หมายถึง", "หมายความ", "แปลว่า",
    "แปล", "อะไร", "บ้าง", "ยังไง", "อย่างไร", "ไหม", "มั้ย", "เปล่า", "หรือเปล่า",
    "รายละเอียด", "อยากรู้", "อยากทราบ", "ทราบ", "ช่วย", "บอก", "หน่อย", "ขอ",
    "ครับ", "ค่ะ", "คะ", "ค่า", "นะ", "อะ", "จ้ะ", "จ้า", "ล่ะ", "เหรอ", "กี่",
    "ทั้งหมด", "ประโยค", "บริบท", "อธิบาย", "เพิ่มเติม", "อื่น", "ตัวอย่าง", "สวัสดี",
    # Conversation openers: "ว่าไง ผมอยากถาม" announces a question, it is not one.
    "ถาม", "อยาก", "ไง", "ว่าไง", "สงสัย", "คุย", "หวัดดี", "ขอบคุณ",
    # Questions about the word itself rather than a situation.
    "รากศัพท์", "ลักษณะคำ", "ชนิดคำ", "ประเภทคำ", "แบบไหน", "ไหน", "ออกเสียง",
    "คำอ่าน", "อ่านว่า", "สะกด",
})

BUILTIN_WORDS = FUNCTION_WORDS | QUESTION_WORDS

CONTENT_WEIGHT = 1.0
FUNCTION_WEIGHT = 0.25

_RUN = re.compile(r"[฀-๿]+|[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
_THAI = re.compile(r"[฀-๿]")
_DEFAULT_MAX_WORD = 40


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    known: bool


def _is_word(text: str, words: Container[str]) -> bool:
    return text in BUILTIN_WORDS or text in words


# A word boundary never falls before a following vowel or tone mark, nor right
# after a leading vowel (เ แ โ ใ ไ).
_FOLLOWING_MARKS = frozenset(
    "ะัาำิีึืฺุู"
    "ๅ็่้๊๋์ํ๎"
)
_LEADING_VOWELS = frozenset("เแโใไ")


def _can_break(run: str, index: int) -> bool:
    if index <= 0 or index >= len(run):
        return True
    return run[index] not in _FOLLOWING_MARKS and run[index - 1] not in _LEADING_VOWELS


# Cost added for an unknown chunk of a single character. A lone Thai letter is
# almost never a word, so "ส|ว่า|ง" (two stray letters around a known word)
# must lose to keeping "สว่าง" whole, while real unknown words next to known
# ones ("พ่อ|ขัน|นอต") still segment normally.
_SINGLE_CHAR_PENALTY = 3


def _segment_thai(run: str, words: Container[str], max_word: int) -> list[Token]:
    """Maximal matching: fewest unknown characters (with a penalty for stray
    single letters), then fewest tokens."""
    size = len(run)
    inf = (10 * size + 10, size + 1)
    # State: 0 = last token known, 1 = unknown run of one char, 2 = longer run.
    best = [[inf, inf, inf] for _ in range(size + 1)]
    back: list[list[tuple[int, int, bool] | None]] = [[None, None, None] for _ in range(size + 1)]
    best[0][0] = (0, 0)

    def relax(end: int, state: int, score: tuple[int, int], pointer: tuple[int, int, bool]) -> None:
        if score < best[end][state]:
            best[end][state] = score
            back[end][state] = pointer

    for start in range(size):
        for state in (0, 1, 2):
            cost, tokens = best[start][state]
            if best[start][state] == inf:
                continue
            closing = _SINGLE_CHAR_PENALTY if state == 1 else 0
            if _can_break(run, start):
                for end in range(min(size, start + max_word), start, -1):
                    if _can_break(run, end) and _is_word(run[start:end], words):
                        relax(end, 0, (cost + closing, tokens + 1), (start, state, True))
            next_state = 1 if state == 0 else 2
            relax(
                start + 1, next_state,
                (cost + 1, tokens + (1 if state == 0 else 0)),
                (start, state, False),
            )

    final = [
        (best[size][state][0] + (_SINGLE_CHAR_PENALTY if state == 1 else 0), best[size][state][1], state)
        for state in (0, 1, 2)
    ]
    state = min(final)[2]
    pieces: list[Token] = []
    end = size
    while end > 0:
        pointer = back[end][state]
        assert pointer is not None
        start, previous_state, known = pointer
        pieces.append(Token(run[start:end], known))
        end, state = start, previous_state
    pieces.reverse()

    merged: list[Token] = []
    for piece in pieces:
        if not piece.known and merged and not merged[-1].known:
            merged[-1] = Token(merged[-1].text + piece.text, False)
        else:
            merged.append(piece)
    return merged


def segment(
    text: str, words: Container[str] = frozenset(), *, max_word: int = _DEFAULT_MAX_WORD
) -> list[Token]:
    """Split text into known words and unknown chunks; spaces and punctuation separate runs."""
    tokens: list[Token] = []
    for match in _RUN.finditer(text):
        run = match.group(0)
        if _THAI.match(run):
            tokens.extend(_segment_thai(run, words, max_word))
        else:
            folded = run.casefold()
            tokens.append(Token(folded, _is_word(folded, words)))
    return tokens


def normalize_typing(text: str) -> str:
    """Fix Thai typing slips that break dictionary lookup: เ+เ typed for แ, and
    zero-width characters pasted from other apps."""
    return re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text).replace("เเ", "แ")


def word_role(text: str) -> str:
    """"question", "function" or "content".

    Dictionaries also list phrases built only from grammar words, such as
    มีความหมาย (มี + ความหมาย). Such a phrase plays the role of its parts.
    """
    if text in QUESTION_WORDS:
        return "question"
    if text in FUNCTION_WORDS:
        return "function"
    if _THAI.match(text):
        parts = _segment_thai(text, frozenset(), len(text))
        if len(parts) > 1 and all(part.known for part in parts):
            return "question" if any(part.text in QUESTION_WORDS for part in parts) else "function"
    return "content"


def context_tokens(
    query: str, lemma: str | None, words: Container[str] = frozenset(),
    *, max_word: int = _DEFAULT_MAX_WORD,
) -> list[tuple[str, float]]:
    """Words around the target that can tell senses apart, with a weight each."""
    lexicon: Container[str] = words
    target = (lemma or "").casefold().strip()
    if target:
        lexicon = _WithWord(words, target)
    result: list[tuple[str, float]] = []
    for token in segment(query, lexicon, max_word=max(max_word, len(target))):
        if token.text == target:
            continue
        role = word_role(token.text)
        if role == "question":
            continue
        if role == "function":
            result.append((token.text, FUNCTION_WEIGHT))
        elif len(token.text) >= 2:
            result.append((token.text, CONTENT_WEIGHT))
    return result


def has_context(
    query: str, lemma: str | None, words: Container[str] = frozenset()
) -> bool:
    """True when the message says something beyond the word and the question itself."""
    return any(
        weight == CONTENT_WEIGHT for _, weight in context_tokens(query, lemma, words)
    )


class _WithWord:
    """Container view that adds one word without copying a large lexicon."""

    __slots__ = ("words", "extra")

    def __init__(self, words: Container[str], extra: str) -> None:
        self.words = words
        self.extra = extra

    def __contains__(self, item: object) -> bool:
        return item == self.extra or item in self.words
