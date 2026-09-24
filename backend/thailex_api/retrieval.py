from __future__ import annotations

import re
import unicodedata

from .lexicon import LexiconIndex
from .models import SenseCandidate
from .repository import GraphRepository
from .thai_text import CONTENT_WEIGHT


# A quoted word is unambiguous: คำว่า “ดาว”, คำ "ดาว".
_QUOTED_LEMMA = re.compile(r"คำ(?:ว่า)?\s*[“\"'‘]\s*([^”\"'’\n]{1,50}?)\s*[”\"'’]")
# Thai is written without spaces, so an unquoted run after คำว่า usually
# continues into the question ("คำว่าดาวหมายถึงอะไร"). The run is only a
# search window; the lemma itself is resolved against the graph.
_KHAM_WA_RUN = re.compile(r"คำว่า\s*([A-Za-z฀-๿][A-Za-z0-9฀-๿_-]{0,59})")
_QUESTION_PHRASE = re.compile(
    r"(?:มีความหมาย|ความหมาย|หมายถึง|หมายความ|แปลว่า|คืออะไร|ในประโยค|ในบริบท|มีกี่|อะไร|ยังไง|อย่างไร)"
)
_POLITE_ENDINGS = ("ครับ", "ค่ะ", "คะ")


def explicit_term(query: str) -> tuple[str | None, bool]:
    """Return the text the user marked with คำว่า and whether it was quoted."""
    quoted = _QUOTED_LEMMA.search(query)
    if quoted:
        return quoted.group(1).strip(), True
    run = _KHAM_WA_RUN.search(query)
    return (run.group(1), False) if run else (None, False)


def display_term(query: str) -> str | None:
    """Best-effort readable form of an explicit word that the graph did not match."""
    term, quoted = explicit_term(query)
    if term is None or quoted:
        return term
    guess = _QUESTION_PHRASE.split(term, maxsplit=1)[0]
    for ending in _POLITE_ENDINGS:
        guess = guess.removesuffix(ending)
    return guess or term


_SOURCE_WEIGHT = {
    "organizer": 1.0,
    "lexitron": 0.9,
    "thai-wordnet": 0.65,
    "th-wiktionary": 0.35,
    "en-wiktionary-thai-entries": 0.25,
    "wiktionary": 0.25,
    "demo": 0.0,
}


def _trigrams(value: str) -> set[str]:
    compact = "".join(value.split())
    return {compact[index : index + 3] for index in range(max(0, len(compact) - 2))}


def context_match(
    context: list[tuple[str, float]], candidate: SenseCandidate
) -> tuple[float, list[str]]:
    """Weighted share of the context words that this sense's evidence contains.

    Context words come from the question with the target word and question
    phrasing removed, so only the situation the user described is compared.
    Unknown multi-word chunks that do not appear whole get partial credit from
    character trigrams. Returns the score and the content words that matched.
    """
    if not context:
        return 0.0, []
    evidence = unicodedata.normalize("NFC", " ".join(item.text for item in candidate.evidence)).casefold()
    evidence_trigrams = _trigrams(evidence)
    total = matched = 0.0
    cues: list[str] = []
    for word, weight in context:
        total += weight
        if word in evidence:
            matched += weight
            if weight == CONTENT_WEIGHT and word not in cues:
                cues.append(word)
        elif weight == CONTENT_WEIGHT and len(word) > 4:
            grams = _trigrams(word)
            if grams:
                matched += weight * len(grams & evidence_trigrams) / len(grams)
    return (matched / total if total else 0.0), cues


def _completeness(candidate: SenseCandidate) -> float:
    kinds = {item.kind for item in candidate.evidence}
    return (
        (0.75 if "definition" in kinds else 0.5 if "synset-definition" in kinds else 0.0)
        + (0.25 if "example" in kinds else 0.15 if "synset-example" in kinds else 0.0)
    )


def rank_score(context_score: float, candidate: SenseCandidate) -> float:
    """Context decides; completeness and source only break ties between senses."""
    return round(
        0.75 * context_score
        + 0.15 * _completeness(candidate)
        + 0.10 * _SOURCE_WEIGHT.get(candidate.source, 0.1),
        4,
    )


class CandidateRetriever:
    """Retrieves and ranks graph facts without asking an LLM to create facts."""

    def __init__(self, repository: GraphRepository, lexicon: LexiconIndex | None = None) -> None:
        self.repository = repository
        self.lexicon = lexicon or LexiconIndex(repository)

    async def resolve_lemma(self, query: str) -> str | None:
        term, quoted = explicit_term(query)
        if term is not None:
            if quoted:
                return term
            # Longest known lemma at the start of the run. Do not fall back to
            # free mention detection: that would answer about words inside the
            # question such as "หมาย" or "ความหมาย".
            return await self.lexicon.longest_prefix(term)
        mentions = await self.lexicon.mentions(query)
        return mentions[0] if mentions else None

    async def retrieve(
        self, query: str, *, lemma: str | None, max_candidates: int
    ) -> tuple[str | None, list[SenseCandidate]]:
        detected = lemma.strip() if lemma else await self.resolve_lemma(query)
        if not detected:
            return None, []
        candidates = await self.repository.get_senses(detected, limit=100)
        context = await self.lexicon.context_tokens(query, detected)
        ranked: list[SenseCandidate] = []
        for candidate in candidates:
            score, cues = context_match(context, candidate)
            ranked.append(candidate.model_copy(update={
                "context_score": round(score, 4),
                "context_cues": cues,
                "retrieval_score": rank_score(score, candidate),
            }))
        ranked.sort(
            key=lambda item: (item.retrieval_score, len(item.evidence), item.source == "organizer"),
            reverse=True,
        )
        # Keep the shortlist useful for comparison and LLM selection after Full
        # imports. Without source balancing, many high-evidence Senses from one
        # dictionary can consume the limit before WordNet or another edition is
        # represented. Demo data never reserves a slot when real sources exist.
        selected: list[SenseCandidate] = []
        selected_uris: set[str] = set()
        represented_graphs: set[str] = set()
        for candidate in ranked:
            if candidate.source == "demo" or candidate.source_graph in represented_graphs:
                continue
            selected.append(candidate)
            selected_uris.add(candidate.sense_uri)
            represented_graphs.add(candidate.source_graph)
            if len(selected) >= max_candidates:
                return detected, selected
        for candidate in ranked:
            if candidate.sense_uri in selected_uris:
                continue
            selected.append(candidate)
            selected_uris.add(candidate.sense_uri)
            if len(selected) >= max_candidates:
                break
        return detected, selected
