from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import quote


LEXITRON_SOURCE_URL = (
    "https://opend-portal.nectec.or.th/en/dataset/lexitron-2-0/"
    "resource/6238e10d-3970-47d9-84a4-6b96494ddde7"
)
LEXITRON_DOWNLOAD_URL = (
    "https://opend-portal.nectec.or.th/dataset/"
    "bdd85296-9398-499f-b3a7-aab85042d3f9/resource/"
    "6238e10d-3970-47d9-84a4-6b96494ddde7/download/telex.csv"
)
LEXITRON_GRAPH_IRI = "https://w3id.org/thailex/graph/lexitron"
LEXITRON_LICENSE_NOTE = "License not specified in the NECTEC Data Catalog"

REQUIRED_COLUMNS = (
    "id",
    "t-search",
    "t-entry",
    "e-entry",
    "t-cat",
    "t-syn",
    "t-sample",
    "t-ant",
    "t-def",
)
KNOWN_COLUMNS = REQUIRED_COLUMNS + ("e-related", "t-num", "notes")
NORMALIZED_POS = {
    "N": "noun",
    "V": "verb",
    "ADJ": "adjective",
    "ADV": "adverb",
    "CLAS": "classifier",
    "PRON": "pronoun",
    "PREP": "preposition",
    "CONJ": "conjunction",
    "INT": "interjection",
    "AUX": "verb",
    "QUES": "particle",
    "END": "particle",
    "NEG": "particle",
}
DATA_POLICY_VERSION = "1.0.0"
VALID_POS = {
    "noun",
    "verb",
    "adjective",
    "adverb",
    "classifier",
    "pronoun",
    "preposition",
    "conjunction",
    "interjection",
    "particle",
    "unknown",
}


def load_policy(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        return {
            "policy_id": "thailex-lexitron-ingestion-policy",
            "version": DATA_POLICY_VERSION,
            "required_identity_fields": ["id", "t-search"],
            "content_rules": {
                "definition_present": "definition-bearing",
                "definition_missing_translation_present": "translation-only",
                "definition_and_translation_missing": "reject",
                "generate_definition_from_translation": False,
            },
            "pos_rules": {
                "mapping": dict(NORMALIZED_POS),
                "unmapped_action": "import-as-unknown",
                "preserve_source_pos": True,
            },
        }
    policy = json.loads(path.read_text(encoding="utf-8-sig"))
    required = ("policy_id", "version", "content_rules", "pos_rules")
    missing = [key for key in required if key not in policy]
    if missing:
        raise ValueError(f"LEXiTRON policy is missing keys: {', '.join(missing)}")
    if policy["content_rules"].get("generate_definition_from_translation") is not False:
        raise ValueError("LEXiTRON policy must not generate definitions from translations")
    if policy["pos_rules"].get("unmapped_action") != "import-as-unknown":
        raise ValueError("Only the conservative import-as-unknown POS policy is supported")
    return policy


def _clean(value: Any) -> str:
    return unicodedata.normalize("NFC", "" if value is None else str(value)).strip()


def _split_terms(value: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;|\n]+", _clean(value)):
        term = _clean(part)
        if term and term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


def _split_examples(value: str) -> list[str]:
    examples: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[;|\n]+", _clean(value)):
        example = _clean(part)
        if example and example not in seen:
            seen.add(example)
            examples.append(example)
    return examples


def _dedupe_dicts(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for value in values:
        key = tuple(sorted(value.items()))
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LexitronAdapter:
    """Audit and normalize the Thai-to-English LEXiTRON CSV resource."""

    source = "lexitron"
    edition = "lexitron-2.0"

    def __init__(self, policy: dict[str, Any] | None = None) -> None:
        self.policy = policy or load_policy()
        self.pos_mapping = dict(self.policy["pos_rules"]["mapping"])

    def detect_encoding(self, path: Path) -> tuple[str, bool]:
        raw = path.read_bytes()
        has_utf8_bom = raw.startswith(b"\xef\xbb\xbf")
        candidates = (
            ("utf-8-sig", "cp874", "tis-620")
            if has_utf8_bom
            else ("utf-8", "cp874", "tis-620")
        )
        for encoding in candidates:
            try:
                raw.decode(encoding, errors="strict")
                return encoding, has_utf8_bom
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Could not decode {path} as UTF-8, CP874, or TIS-620")

    def read_rows(self, path: Path) -> tuple[list[str], list[dict[str, str]], str, bool]:
        encoding, has_utf8_bom = self.detect_encoding(path)
        with path.open("r", encoding=encoding, newline="") as stream:
            reader = csv.DictReader(stream)
            headers = list(reader.fieldnames or [])
            rows = []
            for row in reader:
                normalized_row = {
                    header: "" if row.get(header) is None else str(row.get(header))
                    for header in headers
                }
                if row.get(None):
                    normalized_row["__extra_columns__"] = json.dumps(
                        row[None], ensure_ascii=False
                    )
                rows.append(normalized_row)
        return headers, rows, encoding, has_utf8_bom

    def audit(self, path: Path) -> dict[str, Any]:
        headers, rows, encoding, has_utf8_bom = self.read_rows(path)
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in headers]
        unexpected_columns = [column for column in headers if column not in KNOWN_COLUMNS]
        null_counts = {
            column: sum(1 for row in rows if not _clean(row.get(column)))
            for column in headers
        }
        ids = [_clean(row.get("id")) for row in rows if _clean(row.get("id"))]
        id_counts = Counter(ids)
        duplicate_ids = sorted(key for key, count in id_counts.items() if count > 1)
        pos_counts = Counter(_clean(row.get("t-cat")) or "(empty)" for row in rows)
        unknown_pos_counts = {
            key: count
            for key, count in sorted(pos_counts.items())
            if key not in self.pos_mapping
        }
        malformed_rows = sum(1 for row in rows if row.get("__extra_columns__"))
        unique_lemmas = {
            _clean(row.get("t-search")) for row in rows if _clean(row.get("t-search"))
        }
        decision_counts: Counter[str] = Counter()
        record_kind_counts: Counter[str] = Counter()
        quality_flag_counts: Counter[str] = Counter()
        rejected_records: list[dict[str, Any]] = []
        for row_number, row in enumerate(rows, start=2):
            record = self.normalize(row)
            decision_counts[record["policy_decision"]] += 1
            record_kind_counts[record["record_kind"]] += 1
            quality_flag_counts.update(record["quality_flags"])
            if record["policy_decision"] == "reject":
                record_errors, _ = self.validate(record)
                rejected_records.append(
                    {
                        "csv_row_number": row_number,
                        "source_record_id": record["source_record_id"] or None,
                        "lemma": record["lemma"] or None,
                        "source_entry": record["source_entry"],
                        "errors": record_errors,
                    }
                )
        issues = []
        if missing_columns:
            issues.append({"severity": "error", "type": "missing_columns", "values": missing_columns})
        if duplicate_ids:
            issues.append(
                {
                    "severity": "error",
                    "type": "duplicate_ids",
                    "count": len(duplicate_ids),
                    "sample": duplicate_ids[:20],
                }
            )
        if malformed_rows:
            issues.append(
                {"severity": "error", "type": "extra_csv_columns", "count": malformed_rows}
            )
        if unknown_pos_counts:
            issues.append(
                {
                    "severity": "warning",
                    "type": "unmapped_pos_values",
                    "values": unknown_pos_counts,
                }
            )
        issues.append(
            {
                "severity": "warning",
                "type": "license_not_specified",
                "message": LEXITRON_LICENSE_NOTE,
            }
        )

        return {
            "dataset": "LEXiTRON 2.0 Thai-to-English (telex)",
            "source_url": LEXITRON_SOURCE_URL,
            "download_url": LEXITRON_DOWNLOAD_URL,
            "license": LEXITRON_LICENSE_NOTE,
            "file": {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "encoding": encoding,
                "utf8_bom": has_utf8_bom,
                "delimiter": ",",
            },
            "headers": headers,
            "required_headers": list(REQUIRED_COLUMNS),
            "missing_headers": missing_columns,
            "unexpected_headers": unexpected_columns,
            "row_count": len(rows),
            "unique_lemma_count": len(unique_lemmas),
            "duplicate_id_count": len(duplicate_ids),
            "malformed_row_count": malformed_rows,
            "null_counts": null_counts,
            "pos_counts": dict(sorted(pos_counts.items())),
            "unmapped_pos_counts": unknown_pos_counts,
            "data_policy": {
                "policy_id": self.policy["policy_id"],
                "version": self.policy["version"],
                "decision_counts": dict(sorted(decision_counts.items())),
                "record_kind_counts": dict(sorted(record_kind_counts.items())),
                "quality_flag_counts": dict(sorted(quality_flag_counts.items())),
                "importable_record_count": (
                    decision_counts["accept"] + decision_counts["accept-with-warning"]
                ),
                "rejected_record_count": decision_counts["reject"],
                "rejected_records": rejected_records,
            },
            "issues": issues,
            "status": "failed"
            if any(issue["severity"] == "error" for issue in issues)
            else "warning",
        }

    def normalize(self, row: dict[str, str]) -> dict[str, Any]:
        source_record_id = _clean(row.get("id"))
        lemma = _clean(row.get("t-search"))
        source_entry = _clean(row.get("t-entry"))
        written_form = re.sub(r"\s+\d+$", "", source_entry) or lemma
        source_pos = _clean(row.get("t-cat"))
        sense_number = _clean(row.get("t-num"))
        if not sense_number:
            suffix = re.search(r"\s+(\d+)$", source_entry)
            sense_number = suffix.group(1) if suffix else source_record_id

        synonyms = _dedupe_dicts(
            {
                "term": term,
                "sense_id": None,
                "source_record_id": source_record_id or None,
            }
            for term in _split_terms(row.get("t-syn", ""))
        )
        antonyms = _dedupe_dicts(
            {
                "term": term,
                "sense_id": None,
                "source_record_id": source_record_id or None,
            }
            for term in _split_terms(row.get("t-ant", ""))
        )
        english_related = _dedupe_dicts(
            {
                "term": term,
                "language": "en",
                "source_record_id": source_record_id or None,
            }
            for term in _split_terms(row.get("e-related", ""))
        )
        english_entry = _clean(row.get("e-entry"))
        definition = _clean(row.get("t-def")) or None
        pos_status = "mapped" if source_pos in self.pos_mapping else "unmapped"
        quality_flags: list[str] = []
        if definition is None:
            quality_flags.append("missing-definition")
        if not english_entry:
            quality_flags.append("missing-translation")
        if not source_entry:
            quality_flags.append("missing-source-entry")
        if pos_status == "unmapped":
            quality_flags.append("unmapped-pos")

        if definition is not None:
            record_kind = "definition-bearing"
        elif english_entry:
            record_kind = "translation-only"
        else:
            record_kind = "unusable"

        if not source_record_id or not lemma or record_kind == "unusable":
            policy_decision = "reject"
        elif quality_flags:
            policy_decision = "accept-with-warning"
        else:
            policy_decision = "accept"

        return {
            "source": self.source,
            "source_record_id": source_record_id,
            "edition": self.edition,
            "lemma": lemma,
            "written_form": written_form,
            "source_entry": source_entry or None,
            "language": "th",
            "pos": self.pos_mapping.get(source_pos, "unknown"),
            "source_pos": source_pos or None,
            "pos_status": pos_status,
            "sense_number": sense_number,
            "source_note": _clean(row.get("notes")) or None,
            "definition": definition,
            "record_kind": record_kind,
            "policy_decision": policy_decision,
            "quality_flags": quality_flags,
            "examples": _split_examples(row.get("t-sample", "")),
            "synonyms": synonyms,
            "antonyms": antonyms,
            "hypernyms": [],
            "hyponyms": [],
            "translations": (
                [{"term": english_entry, "language": "en"}] if english_entry else []
            ),
            "english_related": english_related,
            "status": "source-fact",
            "source_url": LEXITRON_SOURCE_URL,
            "license": LEXITRON_LICENSE_NOTE,
            "raw_record": {
                key: value for key, value in row.items() if not key.startswith("__")
            },
        }

    def validate(self, record: dict[str, Any]) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        required_text = (
            "source",
            "source_record_id",
            "edition",
            "lemma",
            "written_form",
            "language",
            "sense_number",
            "status",
        )
        for field in required_text:
            if not isinstance(record.get(field), str) or not record[field].strip():
                errors.append(f"{field}: required non-empty string")
        if record.get("pos") not in VALID_POS:
            errors.append(f"pos: unsupported normalized value {record.get('pos')!r}")
        if record.get("pos_status") not in {"mapped", "unmapped"}:
            errors.append("pos_status: expected mapped or unmapped")
        record_kind = record.get("record_kind")
        if record_kind not in {"definition-bearing", "translation-only", "unusable"}:
            errors.append(f"record_kind: unsupported value {record_kind!r}")
        if record_kind == "definition-bearing" and not record.get("definition"):
            errors.append("definition: required for a definition-bearing record")
        if record_kind == "translation-only":
            if record.get("definition") is not None:
                errors.append("definition: must be null for a translation-only record")
            if not record.get("translations"):
                errors.append("translations: required for a translation-only record")
        if record_kind == "unusable":
            errors.append("content: both t-def and e-entry are missing")
        if record.get("policy_decision") == "reject":
            errors.append("policy_decision: record rejected by the data policy")
        elif record.get("policy_decision") not in {"accept", "accept-with-warning"}:
            errors.append("policy_decision: unsupported decision")
        if record.get("status") != "source-fact":
            errors.append("status: LEXiTRON source records must use source-fact")
        for field in (
            "examples", "synonyms", "antonyms", "hypernyms", "hyponyms",
            "translations", "english_related", "quality_flags",
        ):
            if not isinstance(record.get(field), list):
                errors.append(f"{field}: expected array")
        warnings.extend(record.get("quality_flags") or [])
        return errors, warnings


def select_rows(
    rows: Sequence[dict[str, str]],
    requested_lemmas: Sequence[str] | None,
    limit_lemmas: int,
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    if requested_lemmas:
        ordered_lemmas = list(dict.fromkeys(_clean(value) for value in requested_lemmas if _clean(value)))
    else:
        ordered_lemmas = []
        seen: set[str] = set()
        for row in rows:
            lemma = _clean(row.get("t-search"))
            if lemma and lemma not in seen:
                seen.add(lemma)
                ordered_lemmas.append(lemma)
                if len(ordered_lemmas) >= limit_lemmas:
                    break

    selected_set = set(ordered_lemmas)
    selected = [row for row in rows if _clean(row.get("t-search")) in selected_set]
    found = {_clean(row.get("t-search")) for row in selected}
    missing = [lemma for lemma in ordered_lemmas if lemma not in found]
    return selected, ordered_lemmas, missing


def build_lexitron_subset(
    adapter: LexitronAdapter,
    rows: Sequence[dict[str, str]],
    requested_lemmas: Sequence[str] | None = None,
    limit_lemmas: int = 50,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    selected_rows, selected_lemmas, missing_lemmas = select_rows(
        rows, requested_lemmas, limit_lemmas
    )
    valid_records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warning_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for row in selected_rows:
        record = adapter.normalize(row)
        record_errors, record_warnings = adapter.validate(record)
        source_record_id = record.get("source_record_id", "")
        if source_record_id in seen_ids:
            record_errors.append("source_record_id: duplicate within selected subset")
        seen_ids.add(source_record_id)
        warning_counts.update(record_warnings)
        if record_errors:
            errors.append({"source_record_id": source_record_id, "errors": record_errors})
        else:
            valid_records.append(record)

    found_lemmas = {record["lemma"] for record in valid_records}
    return valid_records, selected_rows, {
        "dataset": "LEXiTRON 2.0 Thai-to-English (telex)",
        "graph_iri": LEXITRON_GRAPH_IRI,
        "requested_lemma_count": len(selected_lemmas),
        "requested_lemmas": selected_lemmas,
        "missing_requested_lemmas": missing_lemmas,
        "selected_row_count": len(selected_rows),
        "valid_record_count": len(valid_records),
        "invalid_record_count": len(errors),
        "definition_bearing_count": sum(
            record["record_kind"] == "definition-bearing" for record in valid_records
        ),
        "translation_only_count": sum(
            record["record_kind"] == "translation-only" for record in valid_records
        ),
        "mapped_pos_count": sum(record["pos_status"] == "mapped" for record in valid_records),
        "unmapped_pos_count": sum(record["pos_status"] == "unmapped" for record in valid_records),
        "rejected_record_count": len(errors),
        "data_policy": {
            "policy_id": adapter.policy["policy_id"],
            "version": adapter.policy["version"],
        },
        "unique_lemma_count": len(found_lemmas),
        "translation_count": sum(len(record["translations"]) for record in valid_records),
        "english_related_count": sum(
            len(record["english_related"]) for record in valid_records
        ),
        "synonym_count": sum(len(record["synonyms"]) for record in valid_records),
        "antonym_count": sum(len(record["antonyms"]) for record in valid_records),
        "warning_counts": dict(sorted(warning_counts.items())),
        "errors": errors,
        "status": (
            "failed"
            if not valid_records
            else (
                "warning"
                if errors or missing_lemmas or warning_counts
                else "passed"
            )
        ),
    }


def _literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _iri_segment(value: str) -> str:
    return quote(unicodedata.normalize("NFC", value), safe="")


def _relation_id(source_record_id: str, relation: str, term: str) -> str:
    payload = f"{source_record_id}\0{relation}\0{term}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def records_to_turtle(records: Sequence[dict[str, Any]]) -> str:
    lines = [
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix lexinfo: <https://lexinfo.net/ontology/3.0/lexinfo#> .",
        "@prefix ontolex: <http://www.w3.org/ns/lemon/ontolex#> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix tlkg: <https://w3id.org/thailex/ontology/> .",
        "@prefix vartrans: <http://www.w3.org/ns/lemon/vartrans#> .",
        "",
        "<https://w3id.org/thailex/source/lexitron/dataset> a prov:Entity ;",
        '    dcterms:title "LEXiTRON 2.0 Thai-to-English"@en ;',
        f"    dcterms:source <{LEXITRON_SOURCE_URL}> ;",
        f"    dcterms:rights {_literal(LEXITRON_LICENSE_NOTE)} .",
        "",
        "<https://w3id.org/thailex/edition/lexitron-2.0> a tlkg:DictionaryEdition ;",
        '    dcterms:title "LEXiTRON 2.0"@en ;',
        "    prov:wasDerivedFrom <https://w3id.org/thailex/source/lexitron/dataset> .",
        "",
    ]

    for record in records:
        source_id = _iri_segment(record["source_record_id"])
        lemma_segment = _iri_segment(record["lemma"])
        entry = f"https://w3id.org/thailex/entry/th/{lemma_segment}"
        form = f"https://w3id.org/thailex/form/th/{lemma_segment}"
        sense = f"https://w3id.org/thailex/sense/lexitron/{source_id}"
        concept = f"https://w3id.org/thailex/concept/lexitron/{source_id}"
        definition = f"https://w3id.org/thailex/definition/lexitron/{source_id}"
        source_record = f"https://w3id.org/thailex/source/lexitron/record/{source_id}"
        pos = record["pos"]

        entry_predicates = [
            "a ontolex:LexicalEntry",
            f"rdfs:label {_literal(record['lemma'])}@th",
            f"ontolex:canonicalForm <{form}>",
            f"ontolex:sense <{sense}>",
        ]
        if pos != "unknown":
            entry_predicates.append(f"lexinfo:partOfSpeech lexinfo:{pos}")
        lines.extend(
            [
                f"<{entry}> " + " ;\n    ".join(entry_predicates) + " .",
                f"<{form}> a ontolex:Form ;",
                f"    ontolex:writtenRep {_literal(record['written_form'])}@th .",
            ]
        )
        sense_predicates = [
            "a ontolex:LexicalSense",
            f"ontolex:isSenseOf <{entry}>",
            f"ontolex:reference <{concept}>",
            f"tlkg:sourceRecordId {_literal(record['source_record_id'])}",
            f"tlkg:senseNumber {_literal(str(record['sense_number']))}",
            "tlkg:inEdition <https://w3id.org/thailex/edition/lexitron-2.0>",
            f"tlkg:originalPartOfSpeech {_literal(record.get('source_pos') or '')}",
            f"tlkg:recordKind {_literal(record['record_kind'])}",
            f"tlkg:policyDecision {_literal(record['policy_decision'])}",
            "tlkg:assertionStatus tlkg:SourceFactStatus",
            f"prov:wasDerivedFrom <{source_record}>",
        ]
        if record.get("source_note"):
            sense_predicates.append(
                f"tlkg:sourceNote {_literal(record['source_note'])}"
            )
        if record["definition"] is not None:
            sense_predicates.append(f"tlkg:hasDefinition <{definition}>")
        if pos != "unknown":
            sense_predicates.append(f"lexinfo:partOfSpeech lexinfo:{pos}")
        for example in record["examples"]:
            sense_predicates.append(f"tlkg:exampleText {_literal(example)}@th")
        for quality_flag in record["quality_flags"]:
            sense_predicates.append(f"tlkg:qualityFlag {_literal(quality_flag)}")
        lines.append(f"<{sense}> " + " ;\n    ".join(sense_predicates) + " .")
        lines.extend(
            [
                f"<{concept}> a ontolex:LexicalConcept, skos:Concept ;",
                f"    skos:prefLabel {_literal(record['lemma'])}@th .",
            ]
        )
        if record["definition"] is not None:
            lines.extend(
                [
                f"<{definition}> a tlkg:DefinitionAssertion ;",
                f"    tlkg:definitionText {_literal(record['definition'])}@th ;",
                f"    prov:wasDerivedFrom <{source_record}> ;",
                "    tlkg:assertionStatus tlkg:SourceFactStatus ;",
                "    tlkg:inEdition <https://w3id.org/thailex/edition/lexitron-2.0> .",
                ]
            )
        lines.extend(
            [
                f"<{source_record}> a prov:Entity ;",
                f"    tlkg:sourceRecordId {_literal(record['source_record_id'])} ;",
                f"    dcterms:identifier {_literal(record['source_record_id'])} ;",
                "    prov:wasDerivedFrom <https://w3id.org/thailex/source/lexitron/dataset> .",
            ]
        )

        for relation_name, predicate in (("synonym", "tlkg:synonym"), ("antonym", "tlkg:antonym")):
            for relation in record[f"{relation_name}s"]:
                term = relation["term"]
                related = (
                    "https://w3id.org/thailex/concept/lexitron/term/"
                    + _iri_segment(term)
                )
                assertion = (
                    "https://w3id.org/thailex/relation/lexitron/"
                    + _relation_id(record["source_record_id"], relation_name, term)
                )
                lines.extend(
                    [
                        f"<{concept}> {predicate} <{related}> .",
                        f"<{related}> a ontolex:LexicalConcept, skos:Concept ;",
                        f"    skos:prefLabel {_literal(term)}@th .",
                        f"<{assertion}> a tlkg:RelationAssertion ;",
                        f"    rdf:subject <{concept}> ;",
                        f"    rdf:predicate {predicate} ;",
                        f"    rdf:object <{related}> ;",
                        f"    prov:wasDerivedFrom <{source_record}> ;",
                        "    tlkg:assertionStatus tlkg:SourceFactStatus .",
                    ]
                )

        for translation in record["translations"]:
            term = translation["term"]
            language = translation["language"]
            term_segment = _iri_segment(term)
            translation_id = _relation_id(record["source_record_id"], "translation", term)
            target_entry = f"https://w3id.org/thailex/entry/{language}/{term_segment}"
            target_form = f"https://w3id.org/thailex/form/{language}/{term_segment}"
            translation_uri = f"https://w3id.org/thailex/translation/lexitron/{translation_id}"
            lines.extend(
                [
                    f"<{target_entry}> a ontolex:LexicalEntry ;",
                    f"    rdfs:label {_literal(term)}@{language} ;",
                    f"    ontolex:canonicalForm <{target_form}> .",
                    f"<{target_form}> a ontolex:Form ;",
                    f"    ontolex:writtenRep {_literal(term)}@{language} .",
                    f"<{translation_uri}> a vartrans:Translation ;",
                    f"    vartrans:source <{sense}> ;",
                    f"    vartrans:target <{target_entry}> ;",
                    f"    prov:wasDerivedFrom <{source_record}> .",
                ]
            )
        for relation in record["english_related"]:
            term = relation["term"]
            related = (
                "https://w3id.org/thailex/concept/lexitron/en-related/"
                + _iri_segment(term)
            )
            assertion = (
                "https://w3id.org/thailex/relation/lexitron/"
                + _relation_id(record["source_record_id"], "e-related", term)
            )
            lines.extend(
                [
                    f"<{concept}> tlkg:relatedTerm <{related}> .",
                    f"<{related}> a ontolex:LexicalConcept, skos:Concept ;",
                    f"    skos:prefLabel {_literal(term)}@en .",
                    f"<{assertion}> a tlkg:RelationAssertion ;",
                    f"    rdf:subject <{concept}> ;",
                    "    rdf:predicate tlkg:relatedTerm ;",
                    f"    rdf:object <{related}> ;",
                    '    tlkg:sourceRelationType "e-related" ;',
                    f"    prov:wasDerivedFrom <{source_record}> ;",
                    "    tlkg:assertionStatus tlkg:SourceFactStatus .",
                ]
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_fixture(path: Path, headers: Sequence[str], rows: Sequence[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(headers), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
