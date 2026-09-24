"""Dictionary-style entry for the explore mode: every sense, grouped by source."""

from __future__ import annotations

import asyncio
import re

from .lexicon import LexiconIndex
from .models import (
    DictionaryEntryResponse,
    EntryAlignmentLink,
    EntrySense,
    EntrySourceGroup,
    LanguageDetailValue,
    SearchItem,
    SenseCandidate,
    SenseLanguageDetails,
)
from .repository import GraphRepository
from .retrieval import CandidateRetriever
from .services import _SOURCE_ORDER, _clean_edition, _source_label
from .thai_text import normalize_typing

MAX_ENTRY_SENSES = 60
_ORGANIZER_GRAPH = "https://w3id.org/thailex/graph/organizer/"


def source_group_name(candidate: SenseCandidate) -> str:
    if candidate.source != "organizer":
        return _source_label(candidate.source)
    if candidate.dataset:
        return candidate.dataset
    if candidate.edition:
        return _clean_edition(candidate.edition)
    return candidate.source_graph.removeprefix(_ORGANIZER_GRAPH)


def _natural_key(value: str | None) -> tuple:
    if not value:
        return ((1, ""),)
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", value) if part
    )


def _definition(candidate: SenseCandidate) -> str | None:
    return next(
        (item.text for item in candidate.evidence
         if item.kind in {"definition", "synset-definition"}),
        None,
    )


def _unique_values(values: list[LanguageDetailValue]) -> list[LanguageDetailValue]:
    seen: set[tuple[str, str | None]] = set()
    result: list[LanguageDetailValue] = []
    for item in values:
        key = (item.value, item.language)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


class EntryBuilder:
    def __init__(
        self, repository: GraphRepository, retriever: CandidateRetriever, lexicon: LexiconIndex
    ) -> None:
        self.repository = repository
        self.retriever = retriever
        self.lexicon = lexicon

    async def resolve(self, query: str) -> tuple[str | None, str | None]:
        text = query.strip().strip("“”\"'")
        if await self.lexicon.contains(text):
            return text, "exact"
        lemma = await self.retriever.resolve_lemma(text)
        if lemma is None:
            return None, None
        if len(lemma) < len(text) and not await self.lexicon.looks_like_phrase(text):
            # A misspelling or a made-up word must not be answered as one of its
            # fragments: "หรุณ" is not "รุ" and "ตูกมีด" is not "มีด". Offer
            # suggestions instead.
            return None, None
        return lemma, "sentence"

    async def suggestions(self, query: str) -> list[SearchItem]:
        """Words containing the query, then the query with its last characters
        dropped, so a typo such as "ดาวว" still offers "ดาว" and a made-up
        word such as "หรุณ" offers words that start the same way."""
        text = query.strip()[:100]
        for size in range(len(text), 1, -1):
            if size < 3 or len(text) - size > 5:
                break
            found = await self.repository.search(text[:size], limit=8)
            if found:
                return found
        return []

    async def build(self, query: str) -> DictionaryEntryResponse:
        query = normalize_typing(query)
        lemma, resolved_from = await self.resolve(query)
        if lemma is None:
            return DictionaryEntryResponse(
                query=query, lemma=None, found=False,
                suggestions=await self.suggestions(query),
            )

        senses = await self.repository.get_senses(lemma, limit=MAX_ENTRY_SENSES + 1)
        truncated = len(senses) > MAX_ENTRY_SENSES
        senses = senses[:MAX_ENTRY_SENSES]
        senses = [item for item in senses if item.source != "demo"] or senses
        if not senses:
            suggestions = await self.repository.search(lemma, limit=8)
            return DictionaryEntryResponse(
                query=query, lemma=lemma, found=False, suggestions=suggestions,
            )

        details, alignments = await asyncio.gather(
            self.repository.get_sense_details_batch(senses),
            self.repository.get_alignments(lemma, limit=300),
        )
        by_uri = {item.sense_uri: item for item in senses}
        links: dict[str, list[EntryAlignmentLink]] = {uri: [] for uri in by_uri}
        for alignment in alignments:
            if alignment.review_status == "rejected":
                continue
            pairs = (
                (alignment.left_sense_uri, alignment.right_sense_uri),
                (alignment.right_sense_uri, alignment.left_sense_uri),
            )
            for own, other in pairs:
                if own not in by_uri or other not in by_uri:
                    continue
                other_candidate = by_uri[other]
                links[own].append(EntryAlignmentLink(
                    other_sense_uri=other,
                    other_source_name=source_group_name(other_candidate),
                    other_definition=_definition(other_candidate),
                    relation=alignment.decided_relation or alignment.recommended_relation,
                    review_status="approved" if alignment.review_status == "approved" else "pending",
                    confidence=alignment.confidence,
                ))

        groups: dict[str, EntrySourceGroup] = {}
        for candidate in senses:
            group = groups.get(candidate.source_graph)
            if group is None:
                group = groups[candidate.source_graph] = EntrySourceGroup(
                    source=candidate.source,
                    source_graph=candidate.source_graph,
                    name=source_group_name(candidate),
                    dataset=candidate.dataset,
                    edition=_clean_edition(candidate.edition) if candidate.edition else None,
                    source_url=candidate.source_url,
                    license=candidate.license,
                    attribution=candidate.attribution,
                )
            group.senses.append(EntrySense(
                candidate=candidate,
                details=details.get(candidate.sense_uri) or SenseLanguageDetails(),
                alignments=links[candidate.sense_uri],
            ))
        for group in groups.values():
            group.senses.sort(key=lambda sense: (
                _natural_key(sense.details.sense_number),
                _natural_key(sense.candidate.source_record_id),
                sense.candidate.sense_uri,
            ))
        ordered = sorted(
            groups.values(),
            key=lambda group: (_SOURCE_ORDER.get(group.source, 8), group.name),
        )

        all_details = [sense.details for group in ordered for sense in group.senses]
        parts_of_speech = list(dict.fromkeys(
            sense.candidate.pos for group in ordered for sense in group.senses
            if sense.candidate.pos
        ))
        return DictionaryEntryResponse(
            query=query,
            lemma=lemma,
            found=True,
            resolved_from=resolved_from,
            sense_count=len(senses),
            source_count=len(ordered),
            parts_of_speech=parts_of_speech,
            pronunciations=_unique_values([v for d in all_details for v in d.pronunciations]),
            romanizations=_unique_values([v for d in all_details for v in d.romanizations]),
            groups=ordered,
            truncated=truncated,
        )
