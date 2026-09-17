from __future__ import annotations

import hashlib
import gzip
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import quote


WIKTEXTRACT_SOURCE_URL = "https://kaikki.org/dictionary/Thai/index.html"
WIKTEXTRACT_DOWNLOAD_URL = (
    "https://kaikki.org/dictionary/Thai/kaikki.org-dictionary-Thai.jsonl"
)
WIKTEXTRACT_GRAPH_IRI = "https://w3id.org/thailex/graph/en-wiktionary-thai-entries"
WIKTEXTRACT_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
WIKTIONARY_ATTRIBUTION_URL = "https://en.wiktionary.org/wiki/Wiktionary:Copyrights"
WIKTEXTRACT_DATASET_DATE = "2026-09-09"
WIKTIONARY_DUMP_DATE = "2026-09-02"
WIKTEXTRACT_SHA256 = (
    "413f5f257987b25cffbfc45eacab4dbade2c34603f267d77e5546d789ddd4167"
)
TH_WIKTEXTRACT_SOURCE_URL = "https://kaikki.org/thwiktionary/index.html"
TH_WIKTEXTRACT_DOWNLOAD_URL = (
    "https://kaikki.org/thwiktionary/raw-wiktextract-data.jsonl.gz"
)
TH_WIKTEXTRACT_GRAPH_IRI = "https://w3id.org/thailex/graph/th-wiktionary"
TH_WIKTIONARY_ATTRIBUTION_URL = "https://th.wiktionary.org/wiki/วิกิพจนานุกรม:ลิขสิทธิ์"
TH_WIKTEXTRACT_DATASET_DATE = "2026-09-13"
TH_WIKTIONARY_DUMP_DATE = "2026-09-01"
TH_WIKTEXTRACT_SHA256 = (
    "9292c197d69d0760964f24d5eecfcb09e0a9c9cc57cd370037532225b790e955"
)

POS_MAPPING = {
    "noun": "noun",
    "verb": "verb",
    "adj": "adjective",
    "adv": "adverb",
    "name": "properNoun",
    "classifier": "classifier",
    "num": "numeral",
    "pron": "pronoun",
    "prep": "preposition",
    "conj": "conjunction",
    "intj": "interjection",
    "particle": "particle",
    "det": "determiner",
    "prefix": "prefix",
    "suffix": "suffix",
    "infix": "infix",
    "phrase": "phrase",
    "prep_phrase": "phrase",
    "proverb": "proverb",
    "character": "character",
    "punct": "punctuation",
    "symbol": "symbol",
}

# Wiktextract documents these fields under Sense as sense-disambiguated linkages.
# Identically named fields at entry level are intentionally not imported.
SENSE_RELATION_FIELDS = (
    "synonyms",
    "antonyms",
    "hypernyms",
    "hyponyms",
    "holonyms",
    "meronyms",
    "coordinate_terms",
    "derived",
    "related",
)
RELATION_PREDICATES = {
    "synonyms": "synonym",
    "antonyms": "antonym",
    "hypernyms": "hypernym",
    "hyponyms": "hyponym",
    "holonyms": "holonym",
    "meronyms": "meronym",
    "coordinate_terms": "coordinateTerm",
    "derived": "derivedTerm",
    "related": "relatedTerm",
}


def _clean(value: Any) -> str:
    return unicodedata.normalize("NFC", "" if value is None else str(value)).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(*values: Any, length: int = 24) -> str:
    payload = "\0".join(_clean(value) for value in values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def _literal(value: Any) -> str:
    return json.dumps(_clean(value), ensure_ascii=False)


def _iri_segment(value: Any) -> str:
    return quote(_clean(value), safe="")


def _strings(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return result
    for value in values:
        cleaned = _clean(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _etymology_text(entry: dict[str, Any]) -> str | None:
    direct = _clean(entry.get("etymology_text"))
    if direct:
        return direct
    values = _strings(entry.get("etymology_texts"))
    return "\n".join(values) if values else None


class WiktextractAdapter:
    """Stream, audit and normalize Kaikki/Wiktextract Thai JSONL."""

    source_kind = "community"

    def __init__(self, edition: str = "en") -> None:
        if edition not in {"en", "th"}:
            raise ValueError("edition must be 'en' or 'th'")
        self.wiktionary_edition = edition
        if edition == "th":
            self.source = "th-wiktionary"
            self.edition = f"thwiktionary-{TH_WIKTIONARY_DUMP_DATE}"
            self.graph_iri = TH_WIKTEXTRACT_GRAPH_IRI
            self.source_url = TH_WIKTEXTRACT_SOURCE_URL
            self.download_url = TH_WIKTEXTRACT_DOWNLOAD_URL
            self.attribution = TH_WIKTIONARY_ATTRIBUTION_URL
            self.dataset_date = TH_WIKTEXTRACT_DATASET_DATE
            self.dump_date = TH_WIKTIONARY_DUMP_DATE
            self.expected_sha256: str | None = TH_WIKTEXTRACT_SHA256
            self.gloss_language = "th"
            self.dataset_name = "Thai entries extracted from Thai Wiktionary"
            self.iri_scope = "th-wiktionary"
        else:
            self.source = "en-wiktionary-thai-entries"
            self.edition = f"enwiktionary-{WIKTIONARY_DUMP_DATE}"
            self.graph_iri = WIKTEXTRACT_GRAPH_IRI
            self.source_url = WIKTEXTRACT_SOURCE_URL
            self.download_url = WIKTEXTRACT_DOWNLOAD_URL
            self.attribution = WIKTIONARY_ATTRIBUTION_URL
            self.dataset_date = WIKTEXTRACT_DATASET_DATE
            self.dump_date = WIKTIONARY_DUMP_DATE
            self.expected_sha256 = WIKTEXTRACT_SHA256
            self.gloss_language = "en"
            self.dataset_name = "Thai entries extracted from English Wiktionary"
            self.iri_scope = "en-wiktionary-thai-entries"

    def iter_entries(self, path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
        opener = gzip.open if path.suffix.casefold() == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"Malformed Wiktextract JSON at line {line_number}: {error}"
                    ) from error
                if not isinstance(value, dict):
                    raise ValueError(
                        f"Wiktextract line {line_number} must contain a JSON object"
                    )
                yield line_number, value

    def audit(self, path: Path) -> dict[str, Any]:
        counts: Counter[str] = Counter()
        pos_counts: Counter[str] = Counter()
        sense_relation_counts: Counter[str] = Counter()
        ambiguous_relation_counts: Counter[str] = Counter()
        languages: Counter[str] = Counter()
        issues: list[dict[str, Any]] = []

        try:
            iterator = self.iter_entries(path)
            for _, entry in iterator:
                counts["entry_records"] += 1
                language = _clean(entry.get("lang_code")) or "(missing)"
                languages[language] += 1
                if language != "th":
                    counts["filtered_non_thai_entries"] += 1
                    continue
                counts["thai_entry_records"] += 1
                pos_counts[_clean(entry.get("pos")) or "(missing)"] += 1
                if not _clean(entry.get("word")):
                    counts["entries_missing_word"] += 1
                if not _clean(entry.get("pos")):
                    counts["entries_missing_pos"] += 1
                sounds = entry.get("sounds") or []
                counts["ipa_values"] += sum(
                    1 for sound in sounds
                    if isinstance(sound, dict) and _clean(sound.get("ipa"))
                )
                counts["forms"] += len(entry.get("forms") or [])
                counts["entries_with_etymology"] += bool(_etymology_text(entry))
                for field in SENSE_RELATION_FIELDS:
                    ambiguous_relation_counts[field] += len(entry.get(field) or [])
                senses = entry.get("senses") or []
                counts["senses"] += len(senses)
                if not senses:
                    counts["entries_without_senses"] += 1
                for sense in senses:
                    if not isinstance(sense, dict):
                        counts["invalid_sense_values"] += 1
                        continue
                    counts["senses_with_id"] += bool(_clean(sense.get("id")))
                    counts["glosses"] += len(sense.get("glosses") or [])
                    counts["examples"] += len(sense.get("examples") or [])
                    counts["senses_without_gloss"] += not bool(sense.get("glosses"))
                    for field in SENSE_RELATION_FIELDS:
                        sense_relation_counts[field] += len(sense.get(field) or [])
        except (UnicodeDecodeError, ValueError) as error:
            issues.append({"severity": "error", "type": "invalid_jsonl", "message": str(error)})

        actual_hash = _sha256(path)
        if self.expected_sha256 and actual_hash != self.expected_sha256:
            issues.append(
                {
                    "severity": "error",
                    "type": "sha256_mismatch",
                    "expected": self.expected_sha256,
                    "actual": actual_hash,
                }
            )
        if counts["filtered_non_thai_entries"]:
            issues.append(
                {
                    "severity": "warning",
                    "type": "non_thai_entries_filtered",
                    "count": counts["filtered_non_thai_entries"],
                }
            )
        ambiguous_total = sum(ambiguous_relation_counts.values())
        if ambiguous_total:
            issues.append(
                {
                    "severity": "warning",
                    "type": "entry_level_relations_skipped",
                    "count": ambiguous_total,
                    "reason": "Wiktextract entry-level linkages are not sense-disambiguated",
                }
            )
        if counts["senses_without_gloss"]:
            issues.append(
                {
                    "severity": "warning",
                    "type": "senses_without_gloss",
                    "count": counts["senses_without_gloss"],
                }
            )

        return {
            "dataset": self.dataset_name,
            "source_kind": "community",
            "source_url": self.source_url,
            "download_url": self.download_url,
            "extraction_date": self.dataset_date,
            "wiktionary_dump_date": self.dump_date,
            "license": WIKTEXTRACT_LICENSE_URL,
            "attribution": self.attribution,
            "file": {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": actual_hash,
                "expected_sha256": self.expected_sha256,
                "encoding": "utf-8",
                "format": "jsonl",
            },
            "counts": dict(sorted(counts.items())),
            "language_counts": dict(sorted(languages.items())),
            "pos_counts": dict(sorted(pos_counts.items())),
            "sense_relation_counts": dict(sorted(sense_relation_counts.items())),
            "skipped_ambiguous_relation_counts": dict(
                sorted(ambiguous_relation_counts.items())
            ),
            "relation_policy": {
                "sense_level": "import-as-source-fact",
                "entry_level": "skip-and-report",
                "ai_inferred": "not-created-by-ingestion",
            },
            "issues": issues,
            "status": "failed"
            if any(issue["severity"] == "error" for issue in issues)
            else ("warning" if issues else "passed"),
        }

    def normalize_entry(
        self, entry: dict[str, Any], line_number: int
    ) -> list[dict[str, Any]]:
        if _clean(entry.get("lang_code")) != "th":
            return []
        lemma = _clean(entry.get("word"))
        source_pos = _clean(entry.get("pos"))
        etymology_number = entry.get("etymology_number")
        entry_record_id = _stable_id(
            lemma, source_pos, etymology_number or "", line_number
        )
        ipa = []
        for sound in entry.get("sounds") or []:
            if isinstance(sound, dict) and _clean(sound.get("ipa")):
                ipa.append(
                    {
                        "value": _clean(sound["ipa"]),
                        "tags": _strings(sound.get("tags")),
                    }
                )
        forms = []
        for form in entry.get("forms") or []:
            if not isinstance(form, dict) or not _clean(form.get("form")):
                continue
            forms.append(
                {
                    "form": _clean(form["form"]),
                    "roman": _clean(form.get("roman")) or None,
                    "tags": _strings(form.get("tags")),
                }
            )

        records = []
        for index, sense in enumerate(entry.get("senses") or [], start=1):
            if not isinstance(sense, dict):
                continue
            source_record_id = _clean(sense.get("id")) or (
                "generated-" + _stable_id(entry_record_id, index, sense.get("glosses"))
            )
            relations = []
            for field in SENSE_RELATION_FIELDS:
                for relation in sense.get(field) or []:
                    if not isinstance(relation, dict) or not _clean(relation.get("word")):
                        continue
                    relations.append(
                        {
                            "type": RELATION_PREDICATES[field],
                            "source_field": field,
                            "term": _clean(relation["word"]),
                            "target_sense_gloss": _clean(relation.get("sense")) or None,
                            "roman": _clean(relation.get("roman")) or None,
                            "tags": _strings(relation.get("tags")),
                            "status": "source-fact",
                        }
                    )
            examples = []
            for example in sense.get("examples") or []:
                if not isinstance(example, dict) or not _clean(example.get("text")):
                    continue
                examples.append(
                    {
                        "text": _clean(example["text"]),
                        "roman": _clean(example.get("roman")) or None,
                        "translation": _clean(
                            example.get("translation") or example.get("english")
                        ) or None,
                    }
                )
            quality_flags = []
            glosses = _strings(sense.get("glosses"))
            if not glosses:
                quality_flags.append("missing-gloss")
            if source_pos not in POS_MAPPING:
                quality_flags.append("unmapped-pos")
            if not _clean(sense.get("id")):
                quality_flags.append("generated-source-record-id")
            records.append(
                {
                    "source": self.source,
                    "source_kind": self.source_kind,
                    "edition": self.edition,
                    "wiktionary_edition": self.wiktionary_edition,
                    "graph_iri": self.graph_iri,
                    "iri_scope": self.iri_scope,
                    "gloss_language": self.gloss_language,
                    "dataset_name": self.dataset_name,
                    "dataset_date": self.dataset_date,
                    "dump_date": self.dump_date,
                    "source_record_id": source_record_id,
                    "entry_record_id": entry_record_id,
                    "source_line_number": line_number,
                    "lemma": lemma,
                    "language": "th",
                    "pos": POS_MAPPING.get(source_pos, "unknown"),
                    "source_pos": source_pos or None,
                    "sense_number": index,
                    "glosses": glosses,
                    "raw_glosses": _strings(sense.get("raw_glosses")),
                    "ipa": ipa,
                    "forms": forms,
                    "etymology_text": _etymology_text(entry),
                    "examples": examples,
                    "relations": relations,
                    "quality_flags": quality_flags,
                    "status": "source-fact",
                    "source_url": self.source_url,
                    "license": WIKTEXTRACT_LICENSE_URL,
                    "attribution": self.attribution,
                }
            )
        return records

    def validate(self, record: dict[str, Any]) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        for field in (
            "source", "source_kind", "edition", "source_record_id",
            "entry_record_id", "lemma", "language", "pos", "status",
        ):
            if not _clean(record.get(field)):
                errors.append(f"{field}: required non-empty value")
        if record.get("language") != "th":
            errors.append("language: only Thai records are accepted")
        if record.get("source_kind") != "community":
            errors.append("source_kind: must be community")
        if record.get("status") != "source-fact":
            errors.append("status: source data must use source-fact")
        for field in ("glosses", "ipa", "forms", "examples", "relations", "quality_flags"):
            if not isinstance(record.get(field), list):
                errors.append(f"{field}: expected array")
        for relation in record.get("relations") or []:
            if relation.get("type") not in RELATION_PREDICATES.values():
                errors.append(f"relations: unsupported type {relation.get('type')!r}")
            if relation.get("status") != "source-fact":
                errors.append("relations: sense-level source relation must use source-fact")
        return errors, list(record.get("quality_flags") or [])


def build_wiktextract(
    adapter: WiktextractAdapter,
    input_path: Path,
    requested_lemmas: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested = list(dict.fromkeys(_clean(value) for value in requested_lemmas or [] if _clean(value)))
    requested_set = set(requested)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warning_counts: Counter[str] = Counter()
    selected_entry_count = 0
    skipped_ambiguous: Counter[str] = Counter()

    for line_number, entry in adapter.iter_entries(input_path):
        if _clean(entry.get("lang_code")) != "th":
            continue
        if requested_set and _clean(entry.get("word")) not in requested_set:
            continue
        selected_entry_count += 1
        for field in SENSE_RELATION_FIELDS:
            skipped_ambiguous[field] += len(entry.get(field) or [])
        for record in adapter.normalize_entry(entry, line_number):
            record_errors, record_warnings = adapter.validate(record)
            warning_counts.update(record_warnings)
            if record_errors:
                errors.append(
                    {
                        "source_record_id": record.get("source_record_id"),
                        "errors": record_errors,
                    }
                )
            else:
                records.append(record)

    found_lemmas = {record["lemma"] for record in records}
    missing = [lemma for lemma in requested if lemma not in found_lemmas]
    report = {
        "dataset": adapter.dataset_name,
        "graph_iri": adapter.graph_iri,
        "selection_mode": "lemma-subset" if requested else "full",
        "requested_lemmas": requested,
        "missing_requested_lemmas": missing,
        "selected_entry_record_count": selected_entry_count,
        "unique_lemma_count": len(found_lemmas),
        "valid_sense_record_count": len(records),
        "invalid_sense_record_count": len(errors),
        "gloss_count": sum(len(record["glosses"]) for record in records),
        "ipa_count": len({
            (record["entry_record_id"], index)
            for record in records
            for index, _ in enumerate(record["ipa"], start=1)
        }),
        "form_count": len({
            (record["entry_record_id"], index)
            for record in records
            for index, _ in enumerate(record["forms"], start=1)
        }),
        "etymology_entry_record_count": len({
            record["entry_record_id"] for record in records if record["etymology_text"]
        }),
        "example_count": sum(len(record["examples"]) for record in records),
        "sense_relation_count": sum(len(record["relations"]) for record in records),
        "sense_relation_counts": dict(
            sorted(Counter(
                relation["type"]
                for record in records
                for relation in record["relations"]
            ).items())
        ),
        "skipped_ambiguous_relation_count": sum(skipped_ambiguous.values()),
        "skipped_ambiguous_relation_counts": dict(sorted(skipped_ambiguous.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
        "errors": errors,
        "source_kind": "community",
        "license": WIKTEXTRACT_LICENSE_URL,
        "attribution": adapter.attribution,
        "status": "failed"
        if not records or errors or missing
        else ("warning" if warning_counts or sum(skipped_ambiguous.values()) else "passed"),
    }
    return records, report


def records_to_turtle(records: Iterable[dict[str, Any]]) -> str:
    records = list(records)
    if not records:
        return ""
    metadata = records[0]
    scope = metadata.get("iri_scope", "en-wiktionary-thai-entries")
    source_url = metadata.get("source_url", WIKTEXTRACT_SOURCE_URL)
    attribution = metadata.get("attribution", WIKTIONARY_ATTRIBUTION_URL)
    dataset_date = metadata.get("dataset_date", WIKTEXTRACT_DATASET_DATE)
    dump_date = metadata.get("dump_date", WIKTIONARY_DUMP_DATE)
    gloss_language = metadata.get("gloss_language", "en")
    dataset_title = metadata.get("dataset_name", "Thai entries extracted from English Wiktionary")
    dataset_uri = f"https://w3id.org/thailex/source/{scope}/dataset"
    edition_uri = f"https://w3id.org/thailex/edition/{scope}/{dump_date}"
    lines = [
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix lexinfo: <https://lexinfo.net/ontology/3.0/lexinfo#> .",
        "@prefix ontolex: <http://www.w3.org/ns/lemon/ontolex#> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix tlkg: <https://w3id.org/thailex/ontology/> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        f"<{dataset_uri}> a prov:Entity, tlkg:CommunitySource ;",
        f"    dcterms:title {_literal(dataset_title)}@en ;",
        f"    dcterms:source <{source_url}> ;",
        f"    dcterms:license <{WIKTEXTRACT_LICENSE_URL}> ;",
        f"    dcterms:rights <{attribution}> ;",
        f'    dcterms:issued "{dataset_date}"^^xsd:date ;',
        f'    dcterms:bibliographicCitation "Wiktionary dump {dump_date}; extracted by Wiktextract and distributed by Kaikki.org."@en .',
        "",
        f"<{edition_uri}> a tlkg:DictionaryEdition ;",
        f"    dcterms:title {_literal(metadata.get('edition', scope))}@en ;",
        f"    prov:wasDerivedFrom <{dataset_uri}> .",
        "",
    ]

    emitted_entries: set[str] = set()
    emitted_forms: set[str] = set()
    emitted_targets: set[str] = set()
    for record in records:
        lemma_id = _iri_segment(record["lemma"])
        sense_id = _iri_segment(record["source_record_id"])
        entry_record_id = _iri_segment(record["entry_record_id"])
        entry = f"https://w3id.org/thailex/entry/th/{lemma_id}"
        canonical_form = f"https://w3id.org/thailex/form/th/{lemma_id}"
        sense = f"https://w3id.org/thailex/sense/{scope}/{sense_id}"
        concept = f"https://w3id.org/thailex/concept/{scope}/{sense_id}"
        source_record = f"https://w3id.org/thailex/source/{scope}/record/{sense_id}"
        entry_source = f"https://w3id.org/thailex/source/{scope}/entry/{entry_record_id}"

        if entry not in emitted_entries:
            emitted_entries.add(entry)
            lines.extend(
                [
                    f"<{entry}> a ontolex:LexicalEntry ;",
                    f"    rdfs:label {_literal(record['lemma'])}@th ;",
                    f"    ontolex:canonicalForm <{canonical_form}> .",
                    f"<{canonical_form}> a ontolex:Form ;",
                    f"    ontolex:writtenRep {_literal(record['lemma'])}@th .",
                    "",
                ]
            )
        lines.append(f"<{entry}> ontolex:sense <{sense}> .")

        sense_predicates = [
            "a ontolex:LexicalSense, prov:Entity",
            f"ontolex:isSenseOf <{entry}>",
            f"ontolex:reference <{concept}>",
            f"tlkg:sourceRecordId {_literal(record['source_record_id'])}",
            f"tlkg:originalPartOfSpeech {_literal(record['source_pos'] or '')}",
            f"tlkg:policyDecision {_literal('accept-with-warning' if record['quality_flags'] else 'accept')}",
            "tlkg:assertionStatus tlkg:SourceFactStatus",
            f"tlkg:inEdition <{edition_uri}>",
            f"prov:wasDerivedFrom <{source_record}>",
        ]
        if record["pos"] != "unknown":
            sense_predicates.append(
                f"lexinfo:partOfSpeech lexinfo:{record['pos']}"
            )
        sense_predicates.append(f"tlkg:senseNumber {_literal(str(record['sense_number']))}")
        for index, _ in enumerate(record["examples"], start=1):
            example_uri = f"https://w3id.org/thailex/example/{scope}/{sense_id}/{index}"
            sense_predicates.append(f"tlkg:hasExample <{example_uri}>")
        for flag in record["quality_flags"]:
            sense_predicates.append(f"tlkg:qualityFlag {_literal(flag)}")
        lines.append(f"<{sense}> " + " ;\n    ".join(sense_predicates) + " .")
        lines.extend(
            [
                f"<{concept}> a ontolex:LexicalConcept ;",
                f"    skos:prefLabel {_literal(record['lemma'])}@th ;",
                f"    ontolex:isConceptOf <{sense}> .",
                f"<{source_record}> a prov:Entity ;",
                f"    tlkg:sourceRecordId {_literal(record['source_record_id'])} ;",
                f"    prov:specializationOf <{entry_source}> ;",
                f"    prov:wasDerivedFrom <{dataset_uri}> .",
                f"<{entry_source}> a prov:Entity ;",
                f'    tlkg:sourceRecordId {_literal(record["entry_record_id"])} ;',
                f"    prov:wasDerivedFrom <{dataset_uri}> .",
            ]
        )

        for index, example in enumerate(record["examples"], start=1):
            example_uri = f"https://w3id.org/thailex/example/{scope}/{sense_id}/{index}"
            example_predicates = [
                "a tlkg:ExampleAssertion, prov:Entity",
                f"tlkg:exampleText {_literal(example['text'])}@th",
                f"prov:wasDerivedFrom <{source_record}>",
            ]
            if example.get("translation"):
                example_predicates.append(
                    f"tlkg:exampleTranslation {_literal(example['translation'])}@en"
                )
            if example.get("roman"):
                example_predicates.append(
                    f"tlkg:romanization {_literal(example['roman'])}"
                )
            lines.append(f"<{example_uri}> " + " ;\n    ".join(example_predicates) + " .")

        if record.get("etymology_text"):
            lines.append(
                f"<{entry_source}> tlkg:etymologyText {_literal(record['etymology_text'])}@{gloss_language} ."
            )
        for index, item in enumerate(record["ipa"], start=1):
            pronunciation = (
                f"https://w3id.org/thailex/form/{scope}/"
                f"{entry_record_id}/pronunciation-{index}"
            )
            if pronunciation in emitted_forms:
                continue
            emitted_forms.add(pronunciation)
            predicates = [
                "a ontolex:Form",
                f"ontolex:phoneticRep {_literal(item['value'])}",
                f"prov:wasDerivedFrom <{entry_source}>",
            ]
            predicates.extend(f"tlkg:formTag {_literal(tag)}" for tag in item["tags"])
            lines.append(f"<{pronunciation}> " + " ;\n    ".join(predicates) + " .")
            lines.append(f"<{entry}> ontolex:otherForm <{pronunciation}> .")
        for index, item in enumerate(record["forms"], start=1):
            other_form = (
                f"https://w3id.org/thailex/form/{scope}/"
                f"{entry_record_id}/form-{index}"
            )
            if other_form in emitted_forms:
                continue
            emitted_forms.add(other_form)
            predicates = [
                "a ontolex:Form",
                f"ontolex:writtenRep {_literal(item['form'])}",
                f"prov:wasDerivedFrom <{entry_source}>",
            ]
            if item.get("roman"):
                predicates.append(f"tlkg:romanization {_literal(item['roman'])}")
            predicates.extend(f"tlkg:formTag {_literal(tag)}" for tag in item["tags"])
            lines.append(f"<{other_form}> " + " ;\n    ".join(predicates) + " .")
            lines.append(f"<{entry}> ontolex:otherForm <{other_form}> .")

        for index, gloss in enumerate(record["glosses"], start=1):
            definition = (
                f"https://w3id.org/thailex/definition/{scope}/"
                f"{sense_id}/{index}"
            )
            lines.extend(
                [
                    f"<{sense}> tlkg:hasDefinition <{definition}> .",
                    f"<{definition}> a tlkg:DefinitionAssertion, prov:Entity ;",
                    f"    tlkg:definitionText {_literal(gloss)}@{gloss_language} ;",
                    "    tlkg:assertionStatus tlkg:SourceFactStatus ;",
                    f"    tlkg:inEdition <{edition_uri}> ;",
                    f"    prov:wasDerivedFrom <{source_record}> .",
                ]
            )

        for index, relation in enumerate(record["relations"], start=1):
            predicate = relation["type"]
            target_key = _stable_id(
                relation["term"], relation.get("target_sense_gloss") or "", predicate
            )
            target = f"https://w3id.org/thailex/concept/{scope}/target/{target_key}"
            if target not in emitted_targets:
                emitted_targets.add(target)
                lines.extend(
                    [
                        f"<{target}> a ontolex:LexicalConcept ;",
                        f"    skos:prefLabel {_literal(relation['term'])}@th .",
                    ]
                )
                if relation.get("target_sense_gloss"):
                    lines.append(
                        f"<{target}> skos:scopeNote {_literal(relation['target_sense_gloss'])}@{gloss_language} ."
                    )
            assertion_id = _stable_id(record["source_record_id"], predicate, target_key, index)
            assertion = f"https://w3id.org/thailex/assertion/{scope}/{assertion_id}"
            lines.extend(
                [
                    f"<{concept}> tlkg:{predicate} <{target}> .",
                    f"<{assertion}> a tlkg:RelationAssertion, rdf:Statement, prov:Entity ;",
                    f"    rdf:subject <{concept}> ;",
                    f"    rdf:predicate tlkg:{predicate} ;",
                    f"    rdf:object <{target}> ;",
                    f"    tlkg:sourceRelationType {_literal(relation['source_field'])} ;",
                    "    tlkg:assertionStatus tlkg:SourceFactStatus ;",
                    f"    prov:wasDerivedFrom <{source_record}> .",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
