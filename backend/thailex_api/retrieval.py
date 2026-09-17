from __future__ import annotations

import re
import unicodedata

from .models import SenseCandidate
from .repository import GraphRepository


_TOKEN = re.compile(r"[\w\u0E00-\u0E7F]+", re.UNICODE)
_EXPLICIT_LEMMA = re.compile(
    r"คำ(?:ว่า)?\s*[“\"'‘]?([A-Za-z\u0E00-\u0E7F][A-Za-z0-9\u0E00-\u0E7F_-]{0,49})"
)


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold().strip()


def _features(value: str) -> set[str]:
    normalized = normalize_text(value)
    tokens = {token for token in _TOKEN.findall(normalized) if len(token) > 1}
    compact = "".join(normalized.split())
    trigrams = {compact[index : index + 3] for index in range(max(0, len(compact) - 2))}
    return tokens | trigrams


def contextual_score(query: str, candidate: SenseCandidate) -> float:
    query_features = _features(query.replace(candidate.lemma, " "))
    evidence_text = " ".join(item.text for item in candidate.evidence)
    evidence_features = _features(evidence_text)
    overlap = (
        len(query_features & evidence_features) / max(1, len(query_features))
        if query_features
        else 0.0
    )
    kinds = {item.kind for item in candidate.evidence}
    completeness = (
        (0.45 if "definition" in kinds else 0.25 if "synset-definition" in kinds else 0.0)
        + (0.15 if "example" in kinds else 0.08 if "synset-example" in kinds else 0.0)
    )
    source_weight = {
        "organizer": 0.22,
        "lexitron": 0.20,
        "thai-wordnet": 0.14,
        "en-wiktionary-thai-entries": 0.05,
        "th-wiktionary": 0.07,
        "wiktionary": 0.05,
        "demo": 0.00,
    }.get(candidate.source, 0.02)
    return min(1.0, 0.65 * overlap + completeness + source_weight)


class CandidateRetriever:
    """Retrieves and ranks graph facts without asking an LLM to create facts."""

    def __init__(self, repository: GraphRepository) -> None:
        self.repository = repository

    async def retrieve(
        self, query: str, *, lemma: str | None, max_candidates: int
    ) -> tuple[str | None, list[SenseCandidate]]:
        detected = lemma.strip() if lemma else None
        if not detected:
            explicit = _EXPLICIT_LEMMA.search(query)
            detected = explicit.group(1) if explicit else None
        if not detected:
            mentions = await self.repository.detect_mentions(query, limit=5)
            detected = mentions[0] if mentions else None
        if not detected:
            return None, []
        candidates = await self.repository.get_senses(detected, limit=100)
        ranked = [
            candidate.model_copy(update={"retrieval_score": contextual_score(query, candidate)})
            for candidate in candidates
        ]
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
