from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Protocol

from .thai_text import BUILTIN_WORDS, context_tokens, segment, word_role

logger = logging.getLogger(__name__)


class LemmaInventorySource(Protocol):
    async def lemma_inventory(self) -> list[tuple[str, int]]: ...


class LexiconIndex:
    """In-memory lemma list from GraphDB for segmentation and word lookup.

    Loading once and matching in Python replaces per-question SPARQL scans of
    every label. The list is refreshed after `ttl_seconds`, so a newly
    imported dataset becomes searchable without restarting the API.
    """

    def __init__(self, source: LemmaInventorySource, *, ttl_seconds: float = 300.0) -> None:
        self.source = source
        self.ttl_seconds = ttl_seconds
        self._sense_counts: dict[str, int] = {}
        self._max_word = 1
        self._loaded_at: float | None = None
        self._lock = asyncio.Lock()
        self._refresh: asyncio.Task[None] | None = None

    def invalidate(self) -> None:
        self._loaded_at = None

    async def _load(self) -> None:
        counts: dict[str, int] = {}
        for lemma, sense_count in await self.source.lemma_inventory():
            key = lemma.strip()
            if key:
                counts[key] = counts.get(key, 0) + sense_count
        # Built-in question words (e.g. ความหมาย) must fit even when every
        # graph lemma is short.
        self._max_word = min(40, max(len(item) for item in (*counts, *BUILTIN_WORDS)))
        self._sense_counts = counts
        self._loaded_at = monotonic()

    async def _refresh_quietly(self) -> None:
        try:
            async with self._lock:
                await self._load()
        except Exception as exc:  # keep serving the previous list
            logger.warning("Lexicon refresh failed; keeping previous word list: %s", exc)

    async def _ensure_loaded(self) -> None:
        if self._loaded_at is None:
            async with self._lock:
                if self._loaded_at is None:
                    await self._load()
            return
        # A stale list is still correct for almost every word, so refresh in the
        # background instead of making one request wait for the full inventory.
        if monotonic() - self._loaded_at >= self.ttl_seconds and (
            self._refresh is None or self._refresh.done()
        ):
            self._refresh = asyncio.create_task(self._refresh_quietly())

    async def warm(self) -> None:
        """Load at startup so the first user request does not pay for it."""
        try:
            await self._ensure_loaded()
        except Exception as exc:
            logger.warning("Lexicon warm-up failed; it will load on first use: %s", exc)

    async def contains(self, lemma: str) -> bool:
        await self._ensure_loaded()
        return lemma.strip() in self._sense_counts

    async def looks_like_phrase(self, text: str) -> bool:
        """True when the text contains a grammar or question word.

        "ดาวคืออะไร" does and may therefore be answered with a word inside it;
        "ตูกมีด" does not, so it is one word the dictionary either has or lacks.
        """
        await self._ensure_loaded()
        tokens = segment(text, self._sense_counts, max_word=self._max_word)
        return any(word_role(token.text) in {"function", "question"} for token in tokens)

    async def longest_prefix(self, text: str) -> str | None:
        """Longest known lemma that `text` starts with."""
        await self._ensure_loaded()
        for size in range(min(len(text), self._max_word), 0, -1):
            if text[:size] in self._sense_counts:
                return text[:size]
        return None

    async def mentions(self, text: str) -> list[str]:
        """Known content words in the text, most likely target first.

        Function and question words are skipped unless the whole message is
        that one word. In a question the earliest content word wins; in a plain
        sentence, words with more senses (the ones that need disambiguation) win.
        """
        await self._ensure_loaded()
        whole = text.strip()
        if whole in self._sense_counts:
            return [whole]
        found: dict[str, int] = {}
        tokens = segment(text, self._sense_counts, max_word=self._max_word)
        is_question = any(word_role(token.text) == "question" for token in tokens)
        for position, token in enumerate(tokens):
            if (
                token.known
                and token.text in self._sense_counts
                and word_role(token.text) == "content"
                and len(token.text) >= 2
            ):
                found.setdefault(token.text, position)
        if is_question:
            # "อ้วน เป็นลักษณะคำแบบไหน": in a question the asked-about word comes
            # first; a later word (ลักษณะ) may have more senses but is phrasing.
            return sorted(found, key=lambda word: found[word])
        return sorted(
            found,
            key=lambda word: (-self._sense_counts[word], -len(word), found[word]),
        )

    async def context_tokens(self, query: str, lemma: str | None) -> list[tuple[str, float]]:
        await self._ensure_loaded()
        return context_tokens(query, lemma, self._sense_counts, max_word=self._max_word)
