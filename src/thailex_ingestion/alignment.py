from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Sequence


BASE_IRI = "https://w3id.org/thailex"
PROPOSED_GRAPH_IRI = f"{BASE_IRI}/graph/alignment/proposed"
REVIEWED_GRAPH_IRI = f"{BASE_IRI}/graph/alignment/reviewed"
ALIGNMENT_POLICY_VERSION = "1.0.0"
MAX_CANDIDATES_PER_SENSE_SOURCE = 3
MIN_CANDIDATE_CONFIDENCE = 0.35

SOURCE_GRAPHS = {
    f"{BASE_IRI}/graph/lexitron": "lexitron",
    f"{BASE_IRI}/graph/thai-wordnet": "omw-th",
    f"{BASE_IRI}/graph/en-wiktionary-thai-entries": "en-wiktionary-thai-entries",
    f"{BASE_IRI}/graph/th-wiktionary": "th-wiktionary",
}
ORGANIZER_GRAPH_PREFIX = f"{BASE_IRI}/graph/organizer/"

POS_ALIASES = {
    "n": "noun", "noun": "noun", "propernoun": "properNoun", "name": "properNoun",
    "v": "verb", "verb": "verb",
    "a": "adjective", "s": "adjective", "adj": "adjective", "adjective": "adjective",
    "r": "adverb", "adv": "adverb", "adverb": "adverb",
    "clas": "classifier", "classifier": "classifier",
    "pron": "pronoun", "pronoun": "pronoun",
    "prep": "preposition", "preposition": "preposition",
    "conj": "conjunction", "conjunction": "conjunction",
    "int": "interjection", "intj": "interjection", "interjection": "interjection",
    "ques": "particle", "end": "particle", "neg": "particle", "particle": "particle",
    "num": "numeral", "numeral": "numeral",
    "det": "determiner", "determiner": "determiner",
    "phrase": "phrase", "prep_phrase": "phrase", "proverb": "proverb",
    "prefix": "prefix", "suffix": "suffix", "infix": "infix",
    "character": "character", "punct": "punctuation", "punctuation": "punctuation",
    "symbol": "symbol", "unknown": "unknown",
}

ENGLISH_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "to", "with", "one", "someone",
    "something", "used", "especially",
}


def normalize_lemma(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value or "")
    normalized = normalized.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(normalized.strip().split())


def normalize_pos(value: str | None) -> str:
    if not value:
        return "unknown"
    key = re.sub(r"[^a-z_]", "", value.lower())
    return POS_ALIASES.get(key, "unknown")


def source_from_graph(graph: str) -> str | None:
    if graph in SOURCE_GRAPHS:
        return SOURCE_GRAPHS[graph]
    if graph.startswith(ORGANIZER_GRAPH_PREFIX):
        suffix = graph[len(ORGANIZER_GRAPH_PREFIX):]
        return "organizer:" + suffix.replace("/", ":")
    return None


def _sparql(endpoint: str, query: str, timeout: int = 120) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=query.encode("utf-8"),
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/sparql-query; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _binding_value(binding: dict[str, Any], key: str) -> str:
    return binding.get(key, {}).get("value", "")


def _lemma_values(lemmas: Sequence[str]) -> str:
    return " ".join(
        json.dumps(normalize_lemma(lemma), ensure_ascii=False) + "@th"
        for lemma in lemmas
    )


def _source_graph_filter(variable: str = "?graph") -> str:
    fixed = ", ".join(f"<{iri}>" for iri in SOURCE_GRAPHS)
    return (
        f"({variable} IN ({fixed}) || "
        f'STRSTARTS(STR({variable}), "{ORGANIZER_GRAPH_PREFIX}"))'
    )


class GraphSenseProvider:
    """Read normalized source senses from GraphDB without mutating source graphs."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def fetch(self, lemmas: Sequence[str]) -> list[dict[str, Any]]:
        requested = list(dict.fromkeys(normalize_lemma(value) for value in lemmas if normalize_lemma(value)))
        if not requested:
            return []
        query = f"""
PREFIX lexinfo: <https://lexinfo.net/ontology/3.0/lexinfo#>
PREFIX ontolex: <http://www.w3.org/ns/lemon/ontolex#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX tlkg: <https://w3id.org/thailex/ontology/>
SELECT DISTINCT ?graph ?entry ?lemma ?sense ?concept ?recordId ?sourcePos ?sensePos
WHERE {{
  VALUES ?lemma {{ {_lemma_values(requested)} }}
  GRAPH ?graph {{
    ?entry a ontolex:LexicalEntry ; rdfs:label ?lemma ; ontolex:sense ?sense .
    ?sense ontolex:reference ?concept .
    OPTIONAL {{ ?sense tlkg:sourceRecordId ?recordId }}
    OPTIONAL {{ ?sense tlkg:originalPartOfSpeech ?sourcePos }}
    OPTIONAL {{ ?sense lexinfo:partOfSpeech ?sensePos }}
  }}
  FILTER({_source_graph_filter()})
}}
ORDER BY ?graph ?sense
"""
        result = _sparql(self.endpoint, query)
        senses: dict[str, dict[str, Any]] = {}
        for binding in result.get("results", {}).get("bindings", []):
            graph = _binding_value(binding, "graph")
            source = source_from_graph(graph)
            if not source:
                continue
            sense_uri = _binding_value(binding, "sense")
            source_pos = _binding_value(binding, "sourcePos")
            sense_pos = _binding_value(binding, "sensePos").rsplit("#", 1)[-1]
            record = senses.setdefault(
                sense_uri,
                {
                    "sense_uri": sense_uri,
                    "concept_uri": _binding_value(binding, "concept"),
                    "entry_uri": _binding_value(binding, "entry"),
                    "source_graph": graph,
                    "source": source,
                    "source_record_id": _binding_value(binding, "recordId") or sense_uri.rsplit("/", 1)[-1],
                    "lemma": _binding_value(binding, "lemma"),
                    "normalized_lemma": normalize_lemma(_binding_value(binding, "lemma")),
                    "source_pos": source_pos or sense_pos or None,
                    "pos": normalize_pos(source_pos or sense_pos),
                    "evidence": [],
                },
            )
            if record["pos"] == "unknown" and normalize_pos(source_pos or sense_pos) != "unknown":
                record["source_pos"] = source_pos or sense_pos
                record["pos"] = normalize_pos(source_pos or sense_pos)
        self._fetch_evidence(senses)
        return sorted(senses.values(), key=lambda item: (item["normalized_lemma"], item["source"], item["sense_uri"]))

    def _fetch_evidence(self, senses: dict[str, dict[str, Any]]) -> None:
        sense_uris = sorted(senses)
        for start in range(0, len(sense_uris), 100):
            chunk = sense_uris[start:start + 100]
            values = " ".join(f"<{uri}>" for uri in chunk)
            query = f"""
PREFIX ontolex: <http://www.w3.org/ns/lemon/ontolex#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX tlkg: <https://w3id.org/thailex/ontology/>
PREFIX vartrans: <http://www.w3.org/ns/lemon/vartrans#>
SELECT DISTINCT ?sense ?kind ?text WHERE {{
  VALUES ?sense {{ {values} }}
  {{
    GRAPH ?sourceGraph {{ ?sense tlkg:hasDefinition/tlkg:definitionText ?text }}
    FILTER({_source_graph_filter('?sourceGraph')})
    BIND("definition" AS ?kind)
  }} UNION {{
    GRAPH ?sourceGraph {{ ?translation vartrans:source ?sense ; vartrans:target/rdfs:label ?text }}
    FILTER({_source_graph_filter('?sourceGraph')})
    BIND("translation" AS ?kind)
  }} UNION {{
    GRAPH ?sourceGraph {{ ?sense tlkg:exampleText ?text }}
    FILTER({_source_graph_filter('?sourceGraph')})
    BIND("example" AS ?kind)
  }} UNION {{
    GRAPH ?sourceGraph {{ ?sense tlkg:hasExample/tlkg:exampleText ?text }}
    FILTER({_source_graph_filter('?sourceGraph')})
    BIND("example" AS ?kind)
  }} UNION {{
    GRAPH ?sourceGraph {{ ?sense tlkg:hasExample/tlkg:exampleTranslation ?text }}
    FILTER({_source_graph_filter('?sourceGraph')})
    BIND("translation" AS ?kind)
  }} UNION {{
    GRAPH ?sourceGraph {{ ?sense tlkg:exampleTranslation ?text }}
    FILTER({_source_graph_filter('?sourceGraph')})
    BIND("translation" AS ?kind)
  }} UNION {{
    GRAPH <{BASE_IRI}/graph/thai-wordnet> {{
      ?sense ontolex:reference/skos:exactMatch ?englishConcept .
    }}
    GRAPH <{BASE_IRI}/graph/omw-en> {{
      ?englishConcept tlkg:hasSynsetDefinition/tlkg:definitionText ?text .
    }}
    BIND("definition" AS ?kind)
  }} UNION {{
    GRAPH <{BASE_IRI}/graph/thai-wordnet> {{
      ?sense ontolex:reference/skos:exactMatch ?englishConcept .
    }}
    GRAPH <{BASE_IRI}/graph/omw-en> {{
      ?englishConcept (skos:prefLabel|skos:altLabel) ?text .
    }}
    BIND("label" AS ?kind)
  }}
}}
"""
            result = _sparql(self.endpoint, query)
            seen: set[tuple[str, str, str, str]] = set()
            for binding in result.get("results", {}).get("bindings", []):
                sense_uri = _binding_value(binding, "sense")
                if sense_uri not in senses:
                    continue
                text_binding = binding.get("text", {})
                item = {
                    "kind": _binding_value(binding, "kind"),
                    "text": text_binding.get("value", ""),
                    "language": text_binding.get("xml:lang") or text_binding.get("lang") or "und",
                }
                key = (sense_uri, item["kind"], item["text"], item["language"])
                if item["text"] and key not in seen:
                    seen.add(key)
                    senses[sense_uri]["evidence"].append(item)


def pos_compatibility(left: str, right: str) -> float:
    if left == right and left != "unknown":
        return 1.0
    if "unknown" in {left, right}:
        return 0.5
    if {left, right} <= {"noun", "properNoun", "classifier"}:
        return 0.65
    return 0.0


def _normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = re.sub(r"[^\w\u0E00-\u0E7F]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _trigrams(value: str) -> set[str]:
    compact = value.replace(" ", "")
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[index:index + 3] for index in range(len(compact) - 2)}


def text_similarity(left: str, right: str, language: str) -> float:
    left_normalized = _normalized_text(left)
    right_normalized = _normalized_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    if min(len(left_normalized), len(right_normalized)) >= 4 and (
        left_normalized in right_normalized or right_normalized in left_normalized
    ):
        substring_score = 0.86
    else:
        substring_score = 0.0
    sequence_score = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    if language == "en":
        left_tokens -= ENGLISH_STOPWORDS
        right_tokens -= ENGLISH_STOPWORDS
    token_union = left_tokens | right_tokens
    token_score = len(left_tokens & right_tokens) / len(token_union) if token_union else 0.0
    left_tri = _trigrams(left_normalized)
    right_tri = _trigrams(right_normalized)
    tri_union = left_tri | right_tri
    trigram_score = len(left_tri & right_tri) / len(tri_union) if tri_union else 0.0
    return round(max(substring_score, sequence_score, token_score, trigram_score), 6)


def _kind_weight(left: str, right: str) -> float:
    pair = frozenset((left, right))
    if left == right == "definition":
        return 1.0
    if pair == {"translation", "label"}:
        return 1.0
    if pair in ({"translation", "definition"}, {"label", "definition"}):
        return 0.9
    if left == right and left in {"translation", "label"}:
        return 0.95
    if "example" in pair:
        return 0.6
    return 0.8


def evidence_similarity(
    left: dict[str, Any], right: dict[str, Any]
) -> tuple[float, dict[str, Any] | None]:
    best_score = 0.0
    best: dict[str, Any] | None = None
    for left_item in left.get("evidence", []):
        for right_item in right.get("evidence", []):
            left_language = left_item.get("language", "und")
            right_language = right_item.get("language", "und")
            if left_language != right_language or left_language == "und":
                continue
            raw_score = text_similarity(left_item["text"], right_item["text"], left_language)
            weighted = raw_score * _kind_weight(left_item["kind"], right_item["kind"])
            if weighted > best_score:
                best_score = weighted
                best = {
                    "left_kind": left_item["kind"],
                    "left_text": left_item["text"],
                    "right_kind": right_item["kind"],
                    "right_text": right_item["text"],
                    "language": left_language,
                    "raw_similarity": raw_score,
                }
    return round(best_score, 6), best


def _candidate_id(left_uri: str, right_uri: str) -> str:
    ordered = sorted((left_uri, right_uri))
    return hashlib.sha256("\0".join(ordered).encode("utf-8")).hexdigest()[:24]


def score_candidate(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any] | None:
    if left["source"] == right["source"]:
        return None
    if left["normalized_lemma"] != right["normalized_lemma"]:
        return None
    pos_score = pos_compatibility(left["pos"], right["pos"])
    if pos_score == 0.0:
        return None
    semantic_score, best_evidence = evidence_similarity(left, right)
    confidence = round(0.25 + 0.20 * pos_score + 0.55 * semantic_score, 6)
    if confidence < MIN_CANDIDATE_CONFIDENCE:
        return None
    if semantic_score >= 0.92 and pos_score == 1.0:
        recommended = "exactMatch"
    elif confidence >= 0.72:
        recommended = "closeMatch"
    else:
        recommended = "possiblySameSense"
    method_parts = ["lemma-exact"]
    method_parts.append("pos-exact" if pos_score == 1.0 else "pos-compatible")
    method_parts.append("lexical-evidence" if semantic_score else "no-comparable-evidence")
    return {
        "candidate_id": _candidate_id(left["sense_uri"], right["sense_uri"]),
        "normalized_lemma": left["normalized_lemma"],
        "left": left,
        "right": right,
        "pos_compatibility": pos_score,
        "semantic_similarity": semantic_score,
        "confidence": confidence,
        "method": "+".join(method_parts),
        "best_evidence": best_evidence,
        "recommended_relation": recommended,
        "relation": "possiblySameSense",
        "review_status": "pending",
        "reviewer": None,
        "review_note": None,
        "policy_version": ALIGNMENT_POLICY_VERSION,
    }


def generate_candidates(senses: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_lemma: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sense in senses:
        by_lemma[sense["normalized_lemma"]].append(sense)
    selected: dict[str, dict[str, Any]] = {}
    for lemma_senses in by_lemma.values():
        by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for sense in lemma_senses:
            by_source[sense["source"]].append(sense)
        for left_source, right_source in combinations(sorted(by_source), 2):
            pairs = [
                candidate
                for left in by_source[left_source]
                for right in by_source[right_source]
                if (candidate := score_candidate(left, right)) is not None
            ]
            by_left: dict[str, list[dict[str, Any]]] = defaultdict(list)
            by_right: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for candidate in pairs:
                by_left[candidate["left"]["sense_uri"]].append(candidate)
                by_right[candidate["right"]["sense_uri"]].append(candidate)
            for groups in (by_left, by_right):
                for candidates in groups.values():
                    candidates.sort(key=lambda item: (-item["confidence"], item["candidate_id"]))
                    for candidate in candidates[:MAX_CANDIDATES_PER_SENSE_SOURCE]:
                        selected[candidate["candidate_id"]] = candidate
    return sorted(
        selected.values(),
        key=lambda item: (
            item["normalized_lemma"], -item["confidence"], item["candidate_id"]
        ),
    )


def _source_pair(candidate: dict[str, Any]) -> str:
    return "|".join(sorted((candidate["left"]["source"], candidate["right"]["source"])))


def select_review_batch(
    candidates: Sequence[dict[str, Any]],
    lemma_order: Sequence[str],
    *,
    size: int = 40,
    per_lemma: int = 2,
    min_confidence: float = 0.85,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select a deterministic, high-confidence batch with broad lemma coverage."""
    if size < 1 or per_lemma < 1:
        raise ValueError("Review batch size and per_lemma must be positive")
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")

    eligible = [
        candidate for candidate in candidates
        if candidate.get("review_status") == "pending"
        and float(candidate.get("confidence", 0.0)) >= min_confidence
    ]
    by_lemma: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in eligible:
        by_lemma[candidate["normalized_lemma"]].append(candidate)

    ordered_lemmas = list(dict.fromkeys(
        normalize_lemma(lemma) for lemma in lemma_order if normalize_lemma(lemma)
    ))
    ordered_lemmas.extend(sorted(set(by_lemma) - set(ordered_lemmas)))
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    selected_by_lemma: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def choice_key(candidate: dict[str, Any], previous: Sequence[dict[str, Any]]) -> tuple[Any, ...]:
        recommendation_priority = {
            "exactMatch": 2,
            "closeMatch": 1,
            "possiblySameSense": 0,
        }.get(candidate["recommended_relation"], 0)
        if not previous:
            return (
                recommendation_priority,
                candidate["confidence"],
                candidate["semantic_similarity"],
                candidate["candidate_id"],
            )
        used_pairs = {_source_pair(item) for item in previous}
        used_senses = {
            sense_uri
            for item in previous
            for sense_uri in (item["left"]["sense_uri"], item["right"]["sense_uri"])
        }
        has_two_new_senses = int(
            candidate["left"]["sense_uri"] not in used_senses
            and candidate["right"]["sense_uri"] not in used_senses
        )
        return (
            int(_source_pair(candidate) not in used_pairs),
            has_two_new_senses,
            recommendation_priority,
            candidate["confidence"],
            candidate["semantic_similarity"],
            candidate["candidate_id"],
        )

    for round_number in range(1, per_lemma + 1):
        for lemma in ordered_lemmas:
            if len(selected) >= size:
                break
            options = [
                candidate for candidate in by_lemma.get(lemma, [])
                if candidate["candidate_id"] not in selected_ids
            ]
            if not options:
                continue
            chosen = max(options, key=lambda item: choice_key(item, selected_by_lemma[lemma]))
            batch_item = dict(chosen)
            batch_item["batch_rank"] = len(selected) + 1
            batch_item["selection_round"] = round_number
            selected.append(batch_item)
            selected_ids.add(chosen["candidate_id"])
            selected_by_lemma[lemma].append(chosen)
        if len(selected) >= size:
            break

    if len(selected) < size:
        remaining = sorted(
            (candidate for candidate in eligible if candidate["candidate_id"] not in selected_ids),
            key=lambda item: (
                -item["confidence"],
                -item["semantic_similarity"],
                item["normalized_lemma"],
                item["candidate_id"],
            ),
        )
        for candidate in remaining[:size - len(selected)]:
            batch_item = dict(candidate)
            batch_item["batch_rank"] = len(selected) + 1
            batch_item["selection_round"] = "fill"
            selected.append(batch_item)

    lemma_counts = Counter(item["normalized_lemma"] for item in selected)
    pair_counts = Counter(_source_pair(item) for item in selected)
    recommendation_counts = Counter(item["recommended_relation"] for item in selected)
    confidence_values = [float(item["confidence"]) for item in selected]
    report = {
        "status": "passed" if len(selected) == min(size, len(eligible)) else "warning",
        "requested_size": size,
        "selected_count": len(selected),
        "eligible_count": len(eligible),
        "minimum_confidence": min_confidence,
        "per_lemma_target": per_lemma,
        "lemma_count": len(lemma_counts),
        "lemma_counts": dict(lemma_counts),
        "source_pair_counts": dict(sorted(pair_counts.items())),
        "recommendation_counts": dict(sorted(recommendation_counts.items())),
        "confidence": {
            "minimum": min(confidence_values) if confidence_values else None,
            "maximum": max(confidence_values) if confidence_values else None,
            "average": round(sum(confidence_values) / len(confidence_values), 6)
            if confidence_values else None,
        },
        "selection_policy": (
            "Pending candidates at or above the confidence threshold; round-robin lemma "
            "coverage; prefer exact recommendations first and a different source pair/new "
            "senses for subsequent selections. Confidence is a heuristic, not a probability."
        ),
    }
    return selected, report


def load_decisions(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("version") != "1.0" or not isinstance(data.get("decisions"), list):
        raise ValueError("Decision file must use version 1.0 and contain decisions[]")
    decisions: dict[str, dict[str, Any]] = {}
    for decision in data["decisions"]:
        candidate_id = decision.get("candidate_id")
        status = decision.get("review_status")
        relation = decision.get("relation")
        if not candidate_id or candidate_id in decisions:
            raise ValueError("Decision candidate IDs must be present and unique")
        if status not in {"approved", "rejected"}:
            raise ValueError(f"Unsupported review_status for {candidate_id}: {status!r}")
        if status == "approved" and relation not in {"exactMatch", "closeMatch"}:
            raise ValueError(f"Approved decision {candidate_id} needs exactMatch or closeMatch")
        if status == "rejected" and relation is not None:
            raise ValueError(f"Rejected decision {candidate_id} must set relation to null")
        reviewer = str(decision.get("reviewer") or "").strip()
        if not reviewer:
            raise ValueError(f"Decision {candidate_id} requires reviewer")
        if reviewer.casefold().startswith("ai-"):
            raise ValueError(
                f"Decision {candidate_id} requires a human reviewer; AI review is provisional"
            )
        if not str(decision.get("note") or "").strip():
            raise ValueError(f"Decision {candidate_id} requires a review note")
        decisions[candidate_id] = decision
    return decisions


def apply_decisions(
    candidates: Sequence[dict[str, Any]], decisions: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    known = {candidate["candidate_id"] for candidate in candidates}
    unknown = sorted(set(decisions) - known)
    if unknown:
        raise ValueError(f"Decisions reference unknown/stale candidates: {unknown[:10]}")
    result = []
    for candidate in candidates:
        updated = dict(candidate)
        decision = decisions.get(candidate["candidate_id"])
        if decision:
            updated["review_status"] = decision["review_status"]
            updated["relation"] = (
                decision.get("relation")
                if decision["review_status"] == "approved"
                else candidate["recommended_relation"]
            )
            updated["reviewer"] = decision["reviewer"]
            updated["review_note"] = decision.get("note")
        result.append(updated)
    return result


def build_alignment(
    endpoint: str,
    lemmas: Sequence[str],
    decisions_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    senses = GraphSenseProvider(endpoint).fetch(lemmas)
    candidates = apply_decisions(generate_candidates(senses), load_decisions(decisions_path))
    source_counts = Counter(sense["source"] for sense in senses)
    relation_counts = Counter(candidate["relation"] for candidate in candidates)
    review_counts = Counter(candidate["review_status"] for candidate in candidates)
    recommendation_counts = Counter(candidate["recommended_relation"] for candidate in candidates)
    approved_relation_counts = Counter(
        candidate["relation"] for candidate in candidates
        if candidate["review_status"] == "approved"
    )
    source_pair_counts = Counter(
        f"{candidate['left']['source']}|{candidate['right']['source']}"
        for candidate in candidates
    )
    lemma_candidate_counts = Counter(
        candidate["normalized_lemma"] for candidate in candidates
    )
    confidence_buckets = Counter(
        "high-0.85+" if candidate["confidence"] >= 0.85
        else ("medium-0.65-0.849999" if candidate["confidence"] >= 0.65 else "low-below-0.65")
        for candidate in candidates
    )
    missing_lemmas = sorted(set(normalize_lemma(value) for value in lemmas) - {sense["normalized_lemma"] for sense in senses})
    report = {
        "policy_version": ALIGNMENT_POLICY_VERSION,
        "endpoint": endpoint,
        "requested_lemmas": list(dict.fromkeys(normalize_lemma(value) for value in lemmas)),
        "missing_lemmas": missing_lemmas,
        "source_sense_counts": dict(sorted(source_counts.items())),
        "source_count": len(source_counts),
        "sense_count": len(senses),
        "candidate_count": len(candidates),
        "recommendation_counts": dict(sorted(recommendation_counts.items())),
        "relation_counts": dict(sorted(relation_counts.items())),
        "approved_relation_counts": dict(sorted(approved_relation_counts.items())),
        "candidate_source_pair_counts": dict(sorted(source_pair_counts.items())),
        "candidate_lemma_counts": dict(sorted(lemma_candidate_counts.items())),
        "confidence_bucket_counts": dict(sorted(confidence_buckets.items())),
        "review_status_counts": dict(sorted(review_counts.items())),
        "approved_count": review_counts["approved"],
        "rejected_count": review_counts["rejected"],
        "pending_count": review_counts["pending"],
        "graphs": {"proposed": PROPOSED_GRAPH_IRI, "reviewed": REVIEWED_GRAPH_IRI},
        "policy": {
            "lemma": "Unicode NFC, zero-width removal, whitespace collapse, exact match",
            "pos": "exact, unknown-compatible, or noun/proper-noun/classifier compatible",
            "confidence": "heuristic ranking score, not a calibrated probability",
            "top_k": MAX_CANDIDATES_PER_SENSE_SOURCE,
            "automatic_relation": "possiblySameSense only",
            "exact_or_close": "requires an approved human decision with reviewer identity",
            "llm_promotion": "forbidden",
        },
        "status": "failed" if not senses or not candidates else ("warning" if missing_lemmas else "passed"),
    }
    return candidates, report


def _literal(value: Any) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def candidates_to_turtle(
    candidates: Sequence[dict[str, Any]], review_status: str
) -> str:
    if review_status not in {"pending", "reviewed"}:
        raise ValueError("review_status must be pending or reviewed")
    selected = [
        candidate for candidate in candidates
        if (candidate["review_status"] == "pending") == (review_status == "pending")
    ]
    graph_kind = "proposed" if review_status == "pending" else "reviewed"
    run = f"{BASE_IRI}/alignment/run/{ALIGNMENT_POLICY_VERSION}/{graph_kind}"
    lines = [
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix tlkg: <https://w3id.org/thailex/ontology/> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        f"<{run}> a tlkg:AlignmentRun, prov:Activity ;",
        f"    dcterms:hasVersion {_literal(ALIGNMENT_POLICY_VERSION)} ;",
        f"    tlkg:alignmentMethod {_literal('deterministic-lexical-candidate-ranking')} .",
        "",
    ]
    predicate_map = {
        "possiblySameSense": "tlkg:possiblySameSense",
        "exactMatch": "skos:exactMatch",
        "closeMatch": "skos:closeMatch",
    }
    review_map = {
        "pending": "tlkg:PendingReviewStatus",
        "approved": "tlkg:ApprovedReviewStatus",
        "rejected": "tlkg:RejectedReviewStatus",
    }
    assertion_status_map = {
        "pending": "tlkg:ProposedStatus",
        "approved": "tlkg:ReviewedStatus",
        "rejected": "tlkg:RejectedStatus",
    }
    for candidate in selected:
        predicate = predicate_map[candidate["relation"]]
        assertion = f"{BASE_IRI}/alignment/assertion/{candidate['candidate_id']}"
        left_uri = candidate["left"]["sense_uri"]
        right_uri = candidate["right"]["sense_uri"]
        if candidate["review_status"] != "rejected":
            lines.append(f"<{left_uri}> {predicate} <{right_uri}> .")
        predicates = [
            "a tlkg:AlignmentAssertion, rdf:Statement, prov:Entity",
            f"rdf:subject <{left_uri}>",
            f"rdf:predicate {predicate}",
            f"rdf:object <{right_uri}>",
            f"tlkg:confidence {_literal(candidate['confidence'])}^^xsd:decimal",
            f"tlkg:semanticSimilarity {_literal(candidate['semantic_similarity'])}^^xsd:decimal",
            f"tlkg:posCompatibility {_literal(candidate['pos_compatibility'])}^^xsd:decimal",
            f"tlkg:alignmentMethod {_literal(candidate['method'])}",
            f"tlkg:normalizedLemma {_literal(candidate['normalized_lemma'])}@th",
            f"tlkg:leftSource {_literal(candidate['left']['source'])}",
            f"tlkg:rightSource {_literal(candidate['right']['source'])}",
            f"tlkg:recommendedRelation {_literal(candidate['recommended_relation'])}",
            f"tlkg:reviewStatus {review_map[candidate['review_status']]}",
            f"tlkg:assertionStatus {assertion_status_map[candidate['review_status']]}",
            f"prov:wasGeneratedBy <{run}>",
        ]
        if candidate.get("reviewer"):
            predicates.append(f"tlkg:reviewedBy {_literal(candidate['reviewer'])}")
        if candidate.get("review_note"):
            predicates.append(f"tlkg:reviewNote {_literal(candidate['review_note'])}")
        lines.append(f"<{assertion}> " + " ;\n    ".join(predicates) + " .")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_review_csv(path: Path, candidates: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "candidate_id", "lemma", "left_source", "left_sense", "left_pos",
        "left_evidence", "right_source", "right_sense", "right_pos",
        "right_evidence", "confidence", "semantic_similarity", "pos_compatibility",
        "method", "recommended_relation", "review_status", "decision_relation",
        "reviewer", "review_note",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for candidate in candidates:
            evidence = candidate.get("best_evidence") or {}
            writer.writerow(
                {
                    "candidate_id": candidate["candidate_id"],
                    "lemma": candidate["normalized_lemma"],
                    "left_source": candidate["left"]["source"],
                    "left_sense": candidate["left"]["sense_uri"],
                    "left_pos": candidate["left"]["pos"],
                    "left_evidence": evidence.get("left_text", ""),
                    "right_source": candidate["right"]["source"],
                    "right_sense": candidate["right"]["sense_uri"],
                    "right_pos": candidate["right"]["pos"],
                    "right_evidence": evidence.get("right_text", ""),
                    "confidence": candidate["confidence"],
                    "semantic_similarity": candidate["semantic_similarity"],
                    "pos_compatibility": candidate["pos_compatibility"],
                    "method": candidate["method"],
                    "recommended_relation": candidate["recommended_relation"],
                    "review_status": candidate["review_status"],
                    "decision_relation": candidate["relation"],
                    "reviewer": candidate.get("reviewer") or "",
                    "review_note": candidate.get("review_note") or "",
                }
            )
