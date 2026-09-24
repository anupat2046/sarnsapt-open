from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import quote


BASE_IRI = "https://w3id.org/thailex"
MAPPING_VERSION = "1.0"
SUPPORTED_FORMATS = {"csv", "json", "jsonl", "xml"}
NORMALIZED_POS = {
    "noun", "verb", "adjective", "adverb", "classifier", "pronoun",
    "preposition", "conjunction", "interjection", "particle", "unknown",
}
LIST_FIELDS = ("examples", "synonyms", "antonyms", "hypernyms", "hyponyms")
RELATION_FIELDS = ("synonyms", "antonyms", "hypernyms", "hyponyms")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _clean(value: Any) -> str:
    return unicodedata.normalize("NFC", "" if value is None else str(value)).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _literal(value: Any) -> str:
    return json.dumps(_clean(value), ensure_ascii=False)


def _iri_segment(value: Any) -> str:
    return quote(_clean(value), safe="")


def _stable_id(*values: Any, length: int = 20) -> str:
    payload = "\0".join(_clean(value) for value in values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def _field_spec(mapping: dict[str, Any], field: str) -> dict[str, Any] | None:
    value = mapping.get("fields", {}).get(field)
    if value is None:
        return None
    if isinstance(value, str):
        return {"path": value}
    if isinstance(value, dict):
        return value
    raise ValueError(f"fields.{field} must be a path string or object")


def _validate_mapping(mapping: dict[str, Any]) -> None:
    if mapping.get("mapping_version") != MAPPING_VERSION:
        raise ValueError(f"mapping_version must be {MAPPING_VERSION!r}")
    source = mapping.get("source")
    input_config = mapping.get("input")
    fields = mapping.get("fields")
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    if not isinstance(input_config, dict):
        raise ValueError("input must be an object")
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    for field in ("id", "title", "edition_id", "edition_title", "language"):
        if not _clean(source.get(field)):
            raise ValueError(f"source.{field} is required")
    for field in ("id", "edition_id"):
        value = _clean(source[field])
        if not ID_PATTERN.fullmatch(value):
            raise ValueError(
                f"source.{field} must match {ID_PATTERN.pattern!r}; found {value!r}"
            )
        if value in {"replace-me", "tbd", "unknown"}:
            raise ValueError(f"source.{field} still contains placeholder value {value!r}")
    language = _clean(source["language"])
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)*", language):
        raise ValueError(f"source.language is not a BCP47-like tag: {language!r}")
    input_format = _clean(input_config.get("format")).lower()
    if input_format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"input.format must be one of {sorted(SUPPORTED_FORMATS)}"
        )
    if input_format == "xml" and not _clean(input_config.get("record_path")):
        raise ValueError("input.record_path is required for XML")
    for field in ("source_record_id", "lemma"):
        spec = _field_spec(mapping, field)
        if not spec or not _clean(spec.get("path")):
            raise ValueError(f"fields.{field}.path is required")
    for relation in RELATION_FIELDS:
        spec = _field_spec(mapping, relation)
        if spec and spec.get("sense_disambiguated") not in (True, False, None):
            raise ValueError(
                f"fields.{relation}.sense_disambiguated must be boolean"
            )
    translation_spec = _field_spec(mapping, "translations")
    if translation_spec:
        translation_language = _clean(translation_spec.get("language"))
        if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)*", translation_language):
            raise ValueError(
                "fields.translations.language is required and must be BCP47-like"
            )
    source_url = _clean(source.get("source_url"))
    if source_url and not source_url.startswith(("http://", "https://")):
        raise ValueError("source.source_url must be an HTTP(S) URL or null")
    pos_mapping = mapping.get("pos_mapping", {})
    if not isinstance(pos_mapping, dict):
        raise ValueError("pos_mapping must be an object")
    invalid_pos = sorted(
        {_clean(value) for value in pos_mapping.values()} - NORMALIZED_POS
    )
    if invalid_pos:
        raise ValueError(f"pos_mapping contains unsupported targets: {invalid_pos}")


def load_mapping(path: Path) -> dict[str, Any]:
    mapping = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(mapping, dict):
        raise ValueError("Mapping file must contain a JSON object")
    _validate_mapping(mapping)
    mapping["_mapping_file_sha256"] = _sha256(path)
    return mapping


def graph_iri(mapping: dict[str, Any]) -> str:
    source = mapping["source"]
    return (
        f"{BASE_IRI}/graph/organizer/"
        f"{_iri_segment(source['id'])}/{_iri_segment(source['edition_id'])}"
    )


def _select_json(value: Any, path: str | None) -> list[Any]:
    if not path:
        return value if isinstance(value, list) else [value]
    current = [value]
    for token in path.split("."):
        next_values: list[Any] = []
        for item in current:
            if token == "*":
                if isinstance(item, list):
                    next_values.extend(item)
                elif isinstance(item, dict):
                    next_values.extend(item.values())
            elif isinstance(item, dict) and token in item:
                next_values.append(item[token])
            elif isinstance(item, list) and token.isdigit():
                index = int(token)
                if 0 <= index < len(item):
                    next_values.append(item[index])
        current = next_values
    result: list[Any] = []
    for item in current:
        if isinstance(item, list):
            result.extend(item)
        else:
            result.append(item)
    return result


class OrganizerAdapter:
    format_name = "base"

    def __init__(self, mapping: dict[str, Any]) -> None:
        self.mapping = mapping

    def read_records(self, path: Path) -> tuple[list[Any], dict[str, Any]]:
        raise NotImplementedError

    def raw_record(self, record: Any) -> dict[str, Any]:
        if isinstance(record, dict):
            return record
        return {"value": record}

    def values(self, record: Any, spec: dict[str, Any] | None) -> list[str]:
        if not spec:
            return []
        path = _clean(spec.get("path"))
        selected = _select_json(record, path)
        values: list[str] = []
        separator = spec.get("separator")
        for value in selected:
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                if separator is not None and isinstance(candidate, str):
                    candidates_split = candidate.split(str(separator))
                else:
                    candidates_split = [candidate]
                for part in candidates_split:
                    cleaned = _clean(part)
                    if cleaned and cleaned not in values:
                        values.append(cleaned)
        return values


class OrganizerCSVAdapter(OrganizerAdapter):
    format_name = "csv"

    @staticmethod
    def _detect_encoding(path: Path, requested: str | None) -> str:
        if requested:
            path.read_text(encoding=requested, errors="strict")
            return requested
        raw = path.read_bytes()
        candidates = (
            ("utf-8-sig", "cp874", "tis-620")
            if raw.startswith(b"\xef\xbb\xbf")
            else ("utf-8", "cp874", "tis-620")
        )
        for encoding in candidates:
            try:
                raw.decode(encoding, errors="strict")
                return encoding
            except UnicodeDecodeError:
                continue
        raise ValueError("CSV is not valid UTF-8, CP874 or TIS-620")

    def read_records(self, path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        config = self.mapping["input"]
        encoding = self._detect_encoding(path, config.get("encoding"))
        sample = path.read_text(encoding=encoding, errors="strict")[:65536]
        delimiter = config.get("delimiter")
        if delimiter is None:
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error as error:
                raise ValueError(
                    "CSV delimiter could not be detected; set input.delimiter"
                ) from error
        if not isinstance(delimiter, str) or len(delimiter) != 1:
            raise ValueError("input.delimiter must be one character")
        with path.open("r", encoding=encoding, newline="") as stream:
            reader = csv.DictReader(stream, delimiter=delimiter)
            headers = list(reader.fieldnames or [])
            rows = []
            malformed_row_count = 0
            for row in reader:
                normalized = dict(row)
                extra_values = normalized.pop(None, None)
                if extra_values:
                    malformed_row_count += 1
                    normalized["__extra_columns__"] = extra_values
                rows.append(normalized)
        return rows, {
            "format": "csv",
            "encoding": encoding,
            "delimiter": delimiter,
            "headers": headers,
            "malformed_row_count": malformed_row_count,
        }

    def values(self, record: Any, spec: dict[str, Any] | None) -> list[str]:
        if spec and isinstance(record, dict) and spec.get("path") in record:
            proxy_spec = dict(spec)
            proxy_spec["path"] = "__exact_csv_value__"
            return super().values(
                {"__exact_csv_value__": record.get(spec["path"])}, proxy_spec
            )
        return super().values(record, spec)


class OrganizerJSONAdapter(OrganizerAdapter):
    format_name = "json"

    def read_records(self, path: Path) -> tuple[list[Any], dict[str, Any]]:
        config = self.mapping["input"]
        encoding = config.get("encoding", "utf-8-sig")
        input_format = _clean(config["format"]).lower()
        records: list[Any] = []
        if input_format == "jsonl":
            with path.open("r", encoding=encoding) as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    try:
                        root = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise ValueError(
                            f"Malformed JSONL at line {line_number}: {error}"
                        ) from error
                    records.extend(_select_json(root, config.get("record_path")))
        else:
            root = json.loads(path.read_text(encoding=encoding))
            records = _select_json(root, config.get("record_path"))
        if not all(isinstance(record, dict) for record in records):
            raise ValueError("Every selected JSON record must be an object")
        return records, {
            "format": input_format,
            "encoding": encoding,
            "record_path": config.get("record_path"),
        }


class OrganizerXMLAdapter(OrganizerAdapter):
    format_name = "xml"

    def read_records(self, path: Path) -> tuple[list[ET.Element], dict[str, Any]]:
        config = self.mapping["input"]
        namespaces = config.get("namespaces", {})
        if not isinstance(namespaces, dict):
            raise ValueError("input.namespaces must be an object")
        root = ET.parse(path).getroot()
        records = list(root.findall(config["record_path"], namespaces))
        return records, {
            "format": "xml",
            "encoding": config.get("encoding", "declared-by-xml"),
            "record_path": config["record_path"],
            "namespaces": namespaces,
        }

    def values(self, record: Any, spec: dict[str, Any] | None) -> list[str]:
        if not spec or not isinstance(record, ET.Element):
            return []
        selector = _clean(spec.get("path"))
        namespaces = self.mapping["input"].get("namespaces", {})
        attribute: str | None = None
        if selector.startswith("@"):
            selected: list[ET.Element] = [record]
            attribute = selector[1:]
        elif "/@" in selector:
            element_path, attribute = selector.rsplit("/@", 1)
            selected = list(record.findall(element_path, namespaces))
        else:
            selected = list(record.findall(selector, namespaces))
        raw_values = []
        for element in selected:
            raw_values.append(
                element.attrib.get(attribute, "")
                if attribute
                else "".join(element.itertext())
            )
        proxy = {"values": raw_values}
        proxy_spec = dict(spec)
        proxy_spec["path"] = "values"
        return OrganizerAdapter.values(self, proxy, proxy_spec)

    def raw_record(self, record: Any) -> dict[str, Any]:
        return {"xml": ET.tostring(record, encoding="unicode")}


def adapter_for(mapping: dict[str, Any]) -> OrganizerAdapter:
    input_format = _clean(mapping["input"]["format"]).lower()
    if input_format == "csv":
        return OrganizerCSVAdapter(mapping)
    if input_format in {"json", "jsonl"}:
        return OrganizerJSONAdapter(mapping)
    if input_format == "xml":
        return OrganizerXMLAdapter(mapping)
    raise ValueError(f"Unsupported input format {input_format!r}")


def _first(adapter: OrganizerAdapter, raw: Any, mapping: dict[str, Any], field: str) -> str:
    values = adapter.values(raw, _field_spec(mapping, field))
    return values[0] if values else ""


def normalize_record(
    adapter: OrganizerAdapter,
    raw: Any,
    record_number: int,
) -> tuple[dict[str, Any], Counter[str]]:
    mapping = adapter.mapping
    source = mapping["source"]
    source_record_id = _first(adapter, raw, mapping, "source_record_id")
    lemma = _first(adapter, raw, mapping, "lemma")
    written_form = _first(adapter, raw, mapping, "written_form") or lemma
    source_entry = _first(adapter, raw, mapping, "source_entry")
    source_pos = _first(adapter, raw, mapping, "pos")
    pos_mapping = mapping.get("pos_mapping", {})
    if source_pos in pos_mapping:
        pos = pos_mapping[source_pos]
        pos_status = "mapped"
    elif source_pos in NORMALIZED_POS:
        pos = source_pos
        pos_status = "mapped"
    else:
        pos = "unknown"
        pos_status = "unmapped"
    definition = _first(adapter, raw, mapping, "definition") or None
    source_sense_number = _first(adapter, raw, mapping, "sense_number")
    sense_number = source_sense_number or source_record_id
    pronunciations = adapter.values(raw, _field_spec(mapping, "pronunciations"))
    etymology = _first(adapter, raw, mapping, "etymology") or None
    notes = adapter.values(raw, _field_spec(mapping, "notes"))
    translations: list[dict[str, str]] = []
    translation_spec = _field_spec(mapping, "translations")
    if translation_spec:
        target_language = _clean(translation_spec.get("language"))
        if not target_language:
            raise ValueError("fields.translations.language is required when translations are mapped")
        translations = [
            {"term": term, "language": target_language}
            for term in adapter.values(raw, translation_spec)
        ]

    skipped_relations: Counter[str] = Counter()
    relations: dict[str, list[dict[str, Any]]] = {}
    for field in RELATION_FIELDS:
        spec = _field_spec(mapping, field)
        values = adapter.values(raw, spec)
        if values and (not spec or spec.get("sense_disambiguated") is not True):
            skipped_relations[field] += len(values)
            values = []
        relations[field] = [
            {
                "term": term,
                "sense_id": None,
                "source_record_id": source_record_id or None,
            }
            for term in values
        ]

    quality_flags: list[str] = []
    if not definition:
        quality_flags.append("missing-definition")
    if translation_spec and not translations:
        quality_flags.append("missing-translation")
    if _field_spec(mapping, "source_entry") and not source_entry:
        quality_flags.append("missing-source-entry")
    if pos_status == "unmapped":
        quality_flags.append("unmapped-pos")
    if _field_spec(mapping, "sense_number") and not source_sense_number:
        quality_flags.append("missing-sense-number")

    if definition:
        record_kind = "definition-bearing"
    elif translations:
        record_kind = "translation-only"
    else:
        record_kind = "unusable"
    policy_decision = (
        "reject"
        if not source_record_id or not lemma or record_kind == "unusable"
        else ("accept-with-warning" if quality_flags else "accept")
    )
    record = {
        "source": source["id"],
        "source_record_id": source_record_id,
        "edition": source["edition_id"],
        "lemma": lemma,
        "written_form": written_form,
        "source_entry": source_entry or None,
        "language": source["language"],
        "pos": pos,
        "source_pos": source_pos or None,
        "pos_status": pos_status,
        "sense_number": sense_number,
        "source_sense_number": source_sense_number or None,
        "definition": definition,
        "pronunciations": pronunciations,
        "etymology": etymology,
        "notes": notes,
        "record_kind": record_kind,
        "policy_decision": policy_decision,
        "quality_flags": quality_flags,
        "examples": adapter.values(raw, _field_spec(mapping, "examples")),
        **relations,
        "translations": translations,
        "status": "source-fact",
        "source_url": source.get("source_url"),
        "license": source.get("license"),
        "raw_record": adapter.raw_record(raw),
        "source_record_number": record_number,
    }
    return record, skipped_relations


def validate_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in (
        "source", "source_record_id", "edition", "lemma", "written_form",
        "language", "sense_number", "status",
    ):
        if not _clean(record.get(field)):
            errors.append(f"{field}: required non-empty value")
    if record.get("pos") not in NORMALIZED_POS:
        errors.append(f"pos: unsupported normalized POS {record.get('pos')!r}")
    if record.get("record_kind") == "unusable":
        errors.append("content: definition and translation are both missing")
    if record.get("policy_decision") == "reject":
        errors.append("policy_decision: rejected by organizer ingestion policy")
    for field in (*LIST_FIELDS, "translations", "quality_flags"):
        if not isinstance(record.get(field), list):
            errors.append(f"{field}: expected array")
    return errors


def audit_dataset(
    input_path: Path,
    mapping_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], OrganizerAdapter, list[Any]]:
    mapping = load_mapping(mapping_path)
    adapter = adapter_for(mapping)
    raw_records, input_metadata = adapter.read_records(input_path)
    counts: Counter[str] = Counter()
    pos_counts: Counter[str] = Counter()
    field_presence: Counter[str] = Counter()
    skipped_relations: Counter[str] = Counter()
    ids: list[str] = []
    rejected_samples: list[dict[str, Any]] = []

    for index, raw in enumerate(raw_records, start=1):
        counts["source_records"] += 1
        record, skipped = normalize_record(adapter, raw, index)
        skipped_relations.update(skipped)
        for field in mapping["fields"]:
            if adapter.values(raw, _field_spec(mapping, field)):
                field_presence[field] += 1
        if record["source_record_id"]:
            ids.append(record["source_record_id"])
        pos_counts[record["source_pos"] or "(missing)"] += 1
        errors = validate_record(record)
        if errors:
            counts["rejected_records"] += 1
            if len(rejected_samples) < 20:
                rejected_samples.append(
                    {
                        "record_number": index,
                        "source_record_id": record["source_record_id"] or None,
                        "lemma": record["lemma"] or None,
                        "errors": errors,
                    }
                )
        else:
            counts["importable_records"] += 1
        counts["missing_definition"] += record["definition"] is None
        counts["unmapped_pos"] += record["pos_status"] == "unmapped"

    duplicate_ids = sorted(
        value for value, count in Counter(ids).items() if count > 1
    )
    issues: list[dict[str, Any]] = []
    if not raw_records:
        issues.append({"severity": "error", "type": "no_records_selected"})
    if input_metadata.get("malformed_row_count"):
        issues.append(
            {
                "severity": "error",
                "type": "csv_extra_columns",
                "count": input_metadata["malformed_row_count"],
                "message": "Check the delimiter and malformed source rows",
            }
        )
    if duplicate_ids:
        issues.append(
            {
                "severity": "error",
                "type": "duplicate_source_record_ids",
                "count": len(duplicate_ids),
                "sample": duplicate_ids[:20],
            }
        )
    if counts["rejected_records"]:
        issues.append(
            {
                "severity": "warning",
                "type": "records_rejected",
                "count": counts["rejected_records"],
            }
        )
    if counts["unmapped_pos"]:
        issues.append(
            {
                "severity": "warning",
                "type": "unmapped_pos",
                "count": counts["unmapped_pos"],
            }
        )
    if sum(skipped_relations.values()):
        issues.append(
            {
                "severity": "warning",
                "type": "relations_without_confirmed_sense_context_skipped",
                "count": sum(skipped_relations.values()),
            }
        )
    for field in ("source_url", "license", "citation"):
        if not _clean(mapping["source"].get(field)):
            issues.append(
                {
                    "severity": "warning",
                    "type": f"missing_{field}",
                    "message": f"Confirm source.{field} with the organizer before publication",
                }
            )
    status = (
        "failed"
        if any(issue["severity"] == "error" for issue in issues)
        or not counts["importable_records"]
        else ("warning" if issues else "passed")
    )
    report = {
        "dataset": mapping["source"]["title"],
        "edition": mapping["source"]["edition_title"],
        "graph_iri": graph_iri(mapping),
        "input_file": {
            "name": input_path.name,
            "bytes": input_path.stat().st_size,
            "sha256": _sha256(input_path),
            **input_metadata,
        },
        "mapping_file": {
            "name": mapping_path.name,
            "sha256": mapping["_mapping_file_sha256"],
            "version": mapping["mapping_version"],
        },
        "counts": dict(sorted(counts.items())),
        "field_presence_counts": dict(sorted(field_presence.items())),
        "source_pos_counts": dict(sorted(pos_counts.items())),
        "skipped_relation_counts": dict(sorted(skipped_relations.items())),
        "rejected_record_samples": rejected_samples,
        "resolved_mapping": {
            "source": mapping["source"],
            "input": mapping["input"],
            "fields": mapping["fields"],
            "pos_mapping": mapping.get("pos_mapping", {}),
        },
        "issues": issues,
        "status": status,
    }
    return report, mapping, adapter, raw_records


def build_dataset(
    input_path: Path,
    mapping_path: Path,
    audit_report_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    prior = json.loads(audit_report_path.read_text(encoding="utf-8-sig"))
    mapping = load_mapping(mapping_path)
    if prior.get("status") == "failed":
        raise ValueError("Audit report failed; resolve its errors before building")
    if prior.get("input_file", {}).get("sha256") != _sha256(input_path):
        raise ValueError("Input file changed after audit; run the audit again")
    if prior.get("mapping_file", {}).get("sha256") != mapping["_mapping_file_sha256"]:
        raise ValueError("Mapping file changed after audit; run the audit again")
    if prior.get("graph_iri") != graph_iri(mapping):
        raise ValueError("Audit graph IRI does not match the current mapping")

    adapter = adapter_for(mapping)
    raw_records, _ = adapter.read_records(input_path)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: Counter[str] = Counter()
    skipped_relations: Counter[str] = Counter()
    for index, raw in enumerate(raw_records, start=1):
        record, skipped = normalize_record(adapter, raw, index)
        skipped_relations.update(skipped)
        record_errors = validate_record(record)
        if record_errors:
            errors.append(
                {
                    "record_number": index,
                    "source_record_id": record["source_record_id"] or None,
                    "errors": record_errors,
                }
            )
            continue
        warnings.update(record["quality_flags"])
        records.append(record)
    report = {
        "dataset": mapping["source"]["title"],
        "edition": mapping["source"]["edition_title"],
        "graph_iri": graph_iri(mapping),
        "source_record_count": len(raw_records),
        "valid_record_count": len(records),
        "invalid_record_count": len(errors),
        "unique_lemma_count": len({record["lemma"] for record in records}),
        "definition_count": sum(record["definition"] is not None for record in records),
        "example_count": sum(len(record["examples"]) for record in records),
        "translation_count": sum(len(record["translations"]) for record in records),
        "relation_count": sum(
            len(record[field]) for record in records for field in RELATION_FIELDS
        ),
        "skipped_relation_count": sum(skipped_relations.values()),
        "skipped_relation_counts": dict(sorted(skipped_relations.items())),
        "warning_counts": dict(sorted(warnings.items())),
        "errors": errors,
        "audit_report": audit_report_path.name,
        "input_sha256": _sha256(input_path),
        "mapping_sha256": mapping["_mapping_file_sha256"],
        "status": "failed" if not records else ("warning" if errors or warnings else "passed"),
    }
    return records, report, mapping


def records_to_turtle(
    records: Sequence[dict[str, Any]], mapping: dict[str, Any]
) -> str:
    source = mapping["source"]
    source_id = _iri_segment(source["id"])
    edition_id = _iri_segment(source["edition_id"])
    dataset = f"{BASE_IRI}/source/organizer/{source_id}/dataset"
    edition = f"{BASE_IRI}/edition/organizer/{source_id}/{edition_id}"
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
    ]
    dataset_predicates = [
        "a prov:Entity, tlkg:OrganizerSource",
        f"dcterms:title {_literal(source['title'])}@{source['language']}",
    ]
    if _clean(source.get("source_url")):
        dataset_predicates.append(f"dcterms:source <{source['source_url']}>")
    if _clean(source.get("license")):
        license_value = source["license"]
        dataset_predicates.append(
            f"dcterms:license <{license_value}>"
            if str(license_value).startswith(("http://", "https://"))
            else f"dcterms:rights {_literal(license_value)}"
        )
    else:
        dataset_predicates.append(
            'dcterms:rights "License metadata not provided; confirm with organizer before publication"@en'
        )
    if _clean(source.get("citation")):
        dataset_predicates.append(
            f"dcterms:bibliographicCitation {_literal(source['citation'])}"
        )
    lines.extend(
        [
            f"<{dataset}> " + " ;\n    ".join(dataset_predicates) + " .",
            f"<{edition}> a tlkg:DictionaryEdition ;",
            f"    dcterms:title {_literal(source['edition_title'])}@{source['language']} ;",
            f"    prov:wasDerivedFrom <{dataset}> .",
            "",
        ]
    )

    for record in records:
        record_id = _iri_segment(record["source_record_id"])
        lemma_id = _iri_segment(record["lemma"])
        entry = f"{BASE_IRI}/entry/{record['language']}/{lemma_id}"
        form = f"{BASE_IRI}/form/{record['language']}/{lemma_id}"
        sense = f"{BASE_IRI}/sense/organizer/{source_id}/{edition_id}/{record_id}"
        concept = f"{BASE_IRI}/concept/organizer/{source_id}/{edition_id}/{record_id}"
        definition = f"{BASE_IRI}/definition/organizer/{source_id}/{edition_id}/{record_id}"
        source_record = f"{BASE_IRI}/source/organizer/{source_id}/{edition_id}/record/{record_id}"
        entry_predicates = [
            "a ontolex:LexicalEntry",
            f"rdfs:label {_literal(record['lemma'])}@{record['language']}",
            f"ontolex:canonicalForm <{form}>",
            f"ontolex:sense <{sense}>",
        ]
        if record["pos"] != "unknown":
            entry_predicates.append(f"lexinfo:partOfSpeech lexinfo:{record['pos']}")
        lines.extend(
            [
                f"<{entry}> " + " ;\n    ".join(entry_predicates) + " .",
                f"<{form}> a ontolex:Form ;",
                f"    ontolex:writtenRep {_literal(record['written_form'])}@{record['language']} .",
            ]
        )
        sense_predicates = [
            "a ontolex:LexicalSense, prov:Entity",
            f"ontolex:isSenseOf <{entry}>",
            f"ontolex:reference <{concept}>",
            f"tlkg:inEdition <{edition}>",
            f"tlkg:sourceRecordId {_literal(record['source_record_id'])}",
            f"tlkg:originalPartOfSpeech {_literal(record['source_pos'] or '')}",
            f"tlkg:recordKind {_literal(record['record_kind'])}",
            f"tlkg:policyDecision {_literal(record['policy_decision'])}",
            "tlkg:assertionStatus tlkg:SourceFactStatus",
            f"prov:wasDerivedFrom <{source_record}>",
        ]
        if record["definition"]:
            sense_predicates.append(f"tlkg:hasDefinition <{definition}>")
        if record["pos"] != "unknown":
            sense_predicates.append(f"lexinfo:partOfSpeech lexinfo:{record['pos']}")
        for example in record["examples"]:
            sense_predicates.append(
                f"tlkg:exampleText {_literal(example)}@{record['language']}"
            )
        for flag in record["quality_flags"]:
            sense_predicates.append(f"tlkg:qualityFlag {_literal(flag)}")
        if record.get("source_sense_number"):
            sense_predicates.append(
                f"tlkg:senseNumber {_literal(record['source_sense_number'])}"
            )
        for note in record.get("notes", []):
            sense_predicates.append(
                f"tlkg:sourceNote {_literal(note)}@{record['language']}"
            )
        lines.extend(
            [
                f"<{sense}> " + " ;\n    ".join(sense_predicates) + " .",
                f"<{concept}> a ontolex:LexicalConcept, skos:Concept ;",
                f"    skos:prefLabel {_literal(record['lemma'])}@{record['language']} .",
            ]
        )
        if record["definition"]:
            lines.extend(
                [
                    f"<{definition}> a tlkg:DefinitionAssertion, prov:Entity ;",
                    f"    tlkg:definitionText {_literal(record['definition'])}@{record['language']} ;",
                    f"    prov:wasDerivedFrom <{source_record}> ;",
                    "    tlkg:assertionStatus tlkg:SourceFactStatus ;",
                    f"    tlkg:inEdition <{edition}> .",
                ]
            )
        for pronunciation_text in record.get("pronunciations", []):
            pronunciation_id = _stable_id(record["source_record_id"], "pronunciation", pronunciation_text)
            pronunciation = (
                f"{BASE_IRI}/form/organizer/{source_id}/{edition_id}/{pronunciation_id}"
            )
            lines.extend(
                [
                    f"<{entry}> ontolex:otherForm <{pronunciation}> .",
                    f"<{pronunciation}> a ontolex:Form ;",
                    f"    ontolex:phoneticRep {_literal(pronunciation_text)} ;",
                    f"    prov:wasDerivedFrom <{source_record}> .",
                ]
            )
        if record.get("etymology"):
            etymology = f"{BASE_IRI}/etymology/organizer/{source_id}/{edition_id}/{record_id}"
            lines.extend(
                [
                    f"<{source_record}> prov:specializationOf <{etymology}> .",
                    f"<{etymology}> a prov:Entity ;",
                    f"    tlkg:etymologyText {_literal(record['etymology'])}@{record['language']} .",
                ]
            )
        raw_text = json.dumps(record["raw_record"], ensure_ascii=False, sort_keys=True)
        lines.extend(
            [
                f"<{source_record}> a prov:Entity ;",
                f"    dcterms:identifier {_literal(record['source_record_id'])} ;",
                f"    tlkg:sourceRecordId {_literal(record['source_record_id'])} ;",
                f"    tlkg:rawRecordText {_literal(raw_text)} ;",
                f"    prov:wasDerivedFrom <{dataset}> .",
            ]
        )
        for relation_field in RELATION_FIELDS:
            predicate = relation_field[:-1] if relation_field.endswith("s") else relation_field
            for relation in record[relation_field]:
                term = relation["term"]
                target_key = _stable_id(relation_field, term)
                target = f"{BASE_IRI}/concept/organizer/{source_id}/{edition_id}/term/{target_key}"
                assertion_id = _stable_id(record["source_record_id"], relation_field, term)
                assertion = f"{BASE_IRI}/assertion/organizer/{source_id}/{edition_id}/{assertion_id}"
                lines.extend(
                    [
                        f"<{concept}> tlkg:{predicate} <{target}> .",
                        f"<{target}> a ontolex:LexicalConcept, skos:Concept ;",
                        f"    skos:prefLabel {_literal(term)}@{record['language']} .",
                        f"<{assertion}> a tlkg:RelationAssertion, rdf:Statement, prov:Entity ;",
                        f"    rdf:subject <{concept}> ;",
                        f"    rdf:predicate tlkg:{predicate} ;",
                        f"    rdf:object <{target}> ;",
                        f"    prov:wasDerivedFrom <{source_record}> ;",
                        "    tlkg:assertionStatus tlkg:SourceFactStatus .",
                    ]
                )
        for translation in record["translations"]:
            term = translation["term"]
            language = translation["language"]
            target_entry = f"{BASE_IRI}/entry/{language}/{_iri_segment(term)}"
            target_form = f"{BASE_IRI}/form/{language}/{_iri_segment(term)}"
            translation_id = _stable_id(record["source_record_id"], "translation", term)
            translation_uri = (
                f"{BASE_IRI}/translation/organizer/{source_id}/{edition_id}/{translation_id}"
            )
            lines.extend(
                [
                    f"<{target_entry}> a ontolex:LexicalEntry ;",
                    f"    rdfs:label {_literal(term)}@{language} ;",
                    f"    ontolex:canonicalForm <{target_form}> .",
                    f"<{target_form}> a ontolex:Form ;",
                    f"    ontolex:writtenRep {_literal(term)}@{language} .",
                    f"<{translation_uri}> a vartrans:Translation, prov:Entity ;",
                    f"    vartrans:source <{sense}> ;",
                    f"    vartrans:target <{target_entry}> ;",
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
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
