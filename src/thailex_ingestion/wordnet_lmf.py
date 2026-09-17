from __future__ import annotations

import hashlib
import json
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import quote


OMW_RELEASE = "2.0"
OMW_RELEASE_URL = "https://github.com/omwn/omw-data/releases/tag/v2.0"
OMW_PROJECT_URL = "https://github.com/omwn/omw-data"
THAI_GRAPH_IRI = "https://w3id.org/thailex/graph/thai-wordnet"
ENGLISH_GRAPH_IRI = "https://w3id.org/thailex/graph/omw-en"
THAI_DEMO_GRAPH_IRI = "https://w3id.org/thailex/graph/thai-wordnet-demo"
ENGLISH_DEMO_GRAPH_IRI = "https://w3id.org/thailex/graph/omw-en-demo"
SELECTED_SYNSET_RELATIONS = {
    "hypernym",
    "hyponym",
    "instance_hypernym",
    "instance_hyponym",
    "similar",
    "mero_member",
    "mero_part",
    "mero_substance",
    "holo_member",
    "holo_part",
    "holo_substance",
    "entails",
    "causes",
    "domain_topic",
    "attribute",
}
POS_MAPPING = {
    "n": "noun",
    "v": "verb",
    "a": "adjective",
    "s": "adjective",
    "r": "adverb",
    "c": "conjunction",
    "p": "preposition",
    "x": "unknown",
    "u": "unknown",
    "z": "unknown",
}
LEXINFO_POS = {
    "noun": "noun",
    "verb": "verb",
    "adjective": "adjective",
    "adverb": "adverb",
    "conjunction": "conjunction",
    "preposition": "preposition",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean(value: Any) -> str:
    return unicodedata.normalize("NFC", "" if value is None else str(value)).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _iri_segment(value: str) -> str:
    return quote(unicodedata.normalize("NFC", value), safe="")


def _relation_id(source_id: str, relation: str, target_id: str) -> str:
    payload = f"{source_id}\0{relation}\0{target_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _children(element: ET.Element, name: str) -> Iterable[ET.Element]:
    return (child for child in element if _local_name(child.tag) == name)


class WordNetLMFParser:
    """Streaming parser for Global WordNet Association WN-LMF XML files."""

    def metadata(self, path: Path) -> dict[str, Any]:
        for event, element in ET.iterparse(path, events=("start",)):
            if event == "start" and _local_name(element.tag) == "Lexicon":
                metadata = {
                    "id": _clean(element.attrib.get("id")),
                    "label": _clean(element.attrib.get("label")),
                    "language": _clean(element.attrib.get("language")),
                    "email": _clean(element.attrib.get("email")),
                    "license": _clean(element.attrib.get("license")),
                    "version": _clean(element.attrib.get("version")),
                    "url": _clean(element.attrib.get("url")),
                    "citation": _clean(element.attrib.get("citation")),
                }
                license_path = path.parent / "LICENSE"
                metadata["license_text"] = (
                    license_path.read_text(encoding="utf-8-sig").strip()
                    if license_path.is_file()
                    else metadata["license"]
                )
                return metadata
        raise ValueError(f"No Lexicon element found in {path}")

    def audit(self, path: Path) -> dict[str, Any]:
        metadata = self.metadata(path)
        counts: Counter[str] = Counter()
        pos_counts: Counter[str] = Counter()
        relation_counts: Counter[str] = Counter()
        missing: Counter[str] = Counter()
        for _, element in ET.iterparse(path, events=("end",)):
            name = _local_name(element.tag)
            if name == "LexicalEntry":
                counts["lexical_entries"] += 1
                lemma = next(_children(element, "Lemma"), None)
                if lemma is None or not _clean(lemma.attrib.get("writtenForm")):
                    missing["lemma"] += 1
                else:
                    pos_counts[_clean(lemma.attrib.get("partOfSpeech")) or "(empty)"] += 1
                senses = list(_children(element, "Sense"))
                counts["senses"] += len(senses)
                missing["sense_synset"] += sum(
                    1 for sense in senses if not _clean(sense.attrib.get("synset"))
                )
                element.clear()
            elif name == "Synset":
                counts["synsets"] += 1
                if not _clean(element.attrib.get("ili")):
                    missing["synset_ili"] += 1
                definitions = list(_children(element, "Definition"))
                examples = list(_children(element, "Example"))
                relations = list(_children(element, "SynsetRelation"))
                counts["definitions"] += len(definitions)
                counts["examples"] += len(examples)
                counts["synset_relations"] += len(relations)
                relation_counts.update(
                    _clean(relation.attrib.get("relType")) or "(empty)"
                    for relation in relations
                )
                element.clear()

        return {
            "metadata": metadata,
            "file": {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "format": "WN-LMF XML 1.4",
            },
            "counts": dict(sorted(counts.items())),
            "part_of_speech_counts": dict(sorted(pos_counts.items())),
            "synset_relation_counts": dict(sorted(relation_counts.items())),
            "missing_counts": dict(sorted(missing.items())),
        }

    def entries(
        self,
        path: Path,
        *,
        lemma_filter: set[str] | None = None,
        synset_filter: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        return list(
            self.iter_entries(
                path, lemma_filter=lemma_filter, synset_filter=synset_filter
            )
        )

    def iter_entries(
        self,
        path: Path,
        *,
        lemma_filter: set[str] | None = None,
        synset_filter: set[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        for _, element in ET.iterparse(path, events=("end",)):
            if _local_name(element.tag) != "LexicalEntry":
                continue
            lemma_node = next(_children(element, "Lemma"), None)
            lemma = _clean(lemma_node.attrib.get("writtenForm")) if lemma_node is not None else ""
            source_pos = _clean(lemma_node.attrib.get("partOfSpeech")) if lemma_node is not None else ""
            senses = [
                {
                    "id": _clean(sense.attrib.get("id")),
                    "synset_id": _clean(sense.attrib.get("synset")),
                    "number": _clean(sense.attrib.get("n")) or None,
                }
                for sense in _children(element, "Sense")
            ]
            if synset_filter is not None:
                senses = [sense for sense in senses if sense["synset_id"] in synset_filter]
            matches_lemma = lemma_filter is None or lemma in lemma_filter
            matches_synset = synset_filter is None or bool(senses)
            if matches_lemma and matches_synset and lemma and senses:
                record = {
                    "id": _clean(element.attrib.get("id")),
                    "lemma": lemma,
                    "source_pos": source_pos,
                    "pos": POS_MAPPING.get(source_pos, "unknown"),
                    "senses": senses,
                }
                element.clear()
                yield record
                continue
            element.clear()

    def synsets(
        self,
        path: Path,
        *,
        id_filter: set[str] | None = None,
        ili_filter: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        return list(self.iter_synsets(path, id_filter=id_filter, ili_filter=ili_filter))

    def iter_synsets(
        self,
        path: Path,
        *,
        id_filter: set[str] | None = None,
        ili_filter: set[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        for _, element in ET.iterparse(path, events=("end",)):
            if _local_name(element.tag) != "Synset":
                continue
            synset_id = _clean(element.attrib.get("id"))
            ili = _clean(element.attrib.get("ili"))
            matches_id = id_filter is None or synset_id in id_filter
            matches_ili = ili_filter is None or ili in ili_filter
            if matches_id and matches_ili:
                record = {
                        "id": synset_id,
                        "ili": ili or None,
                        "source_pos": _clean(element.attrib.get("partOfSpeech")),
                        "pos": POS_MAPPING.get(
                            _clean(element.attrib.get("partOfSpeech")), "unknown"
                        ),
                        "member_ids": [
                            member
                            for member in _clean(element.attrib.get("members")).split()
                            if member
                        ],
                        "definitions": [
                            _clean(definition.text)
                            for definition in _children(element, "Definition")
                            if _clean(definition.text)
                        ],
                        "examples": [
                            _clean(example.text)
                            for example in _children(element, "Example")
                            if _clean(example.text)
                        ],
                        "relations": [
                            {
                                "target": _clean(relation.attrib.get("target")),
                                "type": _clean(relation.attrib.get("relType")),
                            }
                            for relation in _children(element, "SynsetRelation")
                            if _clean(relation.attrib.get("target"))
                            and _clean(relation.attrib.get("relType"))
                        ],
                    }
                element.clear()
                yield record
                continue
            element.clear()


def _attach_lemmas(
    synsets: Sequence[dict[str, Any]], entries: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    lemmas_by_synset: dict[str, list[str]] = {}
    for entry in entries:
        for sense in entry["senses"]:
            lemmas = lemmas_by_synset.setdefault(sense["synset_id"], [])
            if entry["lemma"] not in lemmas:
                lemmas.append(entry["lemma"])
    result = []
    for synset in synsets:
        value = dict(synset)
        value["lemmas"] = lemmas_by_synset.get(synset["id"], [])
        result.append(value)
    return result


def build_omw_subset(
    parser: WordNetLMFParser,
    thai_path: Path,
    english_path: Path,
    requested_lemmas: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    ordered_lemmas = list(dict.fromkeys(_clean(lemma) for lemma in requested_lemmas if _clean(lemma)))
    thai_metadata = parser.metadata(thai_path)
    english_metadata = parser.metadata(english_path)
    thai_entries = parser.entries(thai_path, lemma_filter=set(ordered_lemmas))
    thai_synset_ids = {
        sense["synset_id"] for entry in thai_entries for sense in entry["senses"]
    }
    thai_synsets = parser.synsets(thai_path, id_filter=thai_synset_ids)
    thai_synsets = _attach_lemmas(thai_synsets, thai_entries)
    ili_values = {synset["ili"] for synset in thai_synsets if synset["ili"]}

    aligned_english = parser.synsets(english_path, ili_filter=ili_values)
    aligned_ids = {synset["id"] for synset in aligned_english}
    context_ids = {
        relation["target"]
        for synset in aligned_english
        for relation in synset["relations"]
        if relation["type"] in SELECTED_SYNSET_RELATIONS
    }
    selected_english_ids = aligned_ids | context_ids
    english_synsets = parser.synsets(english_path, id_filter=selected_english_ids)
    english_entries = parser.entries(english_path, synset_filter=selected_english_ids)
    english_synsets = _attach_lemmas(english_synsets, english_entries)

    english_by_ili = {
        synset["ili"]: synset["id"]
        for synset in english_synsets
        if synset["ili"] and synset["id"] in aligned_ids
    }
    for synset in thai_synsets:
        synset["english_synset_id"] = english_by_ili.get(synset["ili"])

    found_lemmas = {entry["lemma"] for entry in thai_entries}
    missing_requested = [lemma for lemma in ordered_lemmas if lemma not in found_lemmas]
    missing_ili = sorted(synset["id"] for synset in thai_synsets if not synset["ili"])
    missing_english = sorted(
        synset["id"] for synset in thai_synsets if not synset["english_synset_id"]
    )
    relation_counts = Counter(
        relation["type"]
        for synset in english_synsets
        if synset["id"] in aligned_ids
        for relation in synset["relations"]
        if relation["type"] in SELECTED_SYNSET_RELATIONS
    )
    report = {
        "release": OMW_RELEASE,
        "requested_lemma_count": len(ordered_lemmas),
        "requested_lemmas": ordered_lemmas,
        "matched_lemma_count": len(found_lemmas),
        "missing_requested_lemmas": missing_requested,
        "thai_entry_count": len(found_lemmas),
        "thai_source_entry_count": len(thai_entries),
        "thai_sense_count": sum(len(entry["senses"]) for entry in thai_entries),
        "thai_synset_count": len(thai_synsets),
        "thai_synsets_without_ili": missing_ili,
        "aligned_english_synset_count": len(aligned_ids),
        "english_synset_count": len(english_synsets),
        "thai_synsets_without_english_match": missing_english,
        "english_context_synset_count": len(selected_english_ids - aligned_ids),
        "english_definition_count": sum(
            len(synset["definitions"])
            for synset in english_synsets
            if synset["id"] in aligned_ids
        ),
        "english_example_count": sum(
            len(synset["examples"])
            for synset in english_synsets
            if synset["id"] in aligned_ids
        ),
        "selected_relation_counts": dict(sorted(relation_counts.items())),
        "selected_relation_types": sorted(SELECTED_SYNSET_RELATIONS),
        "graphs": {
            "thai": THAI_DEMO_GRAPH_IRI,
            "english": ENGLISH_DEMO_GRAPH_IRI,
        },
        "status": "warning" if missing_requested or missing_ili or missing_english else "passed",
    }
    return (
        {"metadata": thai_metadata, "entries": thai_entries, "synsets": thai_synsets},
        {
            "metadata": english_metadata,
            "aligned_synset_ids": sorted(aligned_ids),
            "synsets": english_synsets,
        },
        report,
    )


def _write_jsonl_record(stream: Any, record_type: str, value: dict[str, Any]) -> None:
    json.dump(
        {"record_type": record_type, **value},
        stream,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    stream.write("\n")


def build_omw_full_streaming(
    parser: WordNetLMFParser,
    thai_path: Path,
    english_path: Path,
    thai_normalized_path: Path,
    english_normalized_path: Path,
    thai_rdf_path: Path,
    english_rdf_path: Path,
    *,
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Build full OMW graphs with bounded memory by reparsing XML in streams."""

    started = time.perf_counter()
    thai_metadata = parser.metadata(thai_path)
    english_metadata = parser.metadata(english_path)

    thai_ilis: set[str] = set()
    thai_synset_source_count = 0
    thai_synsets_without_ili = 0
    for synset in parser.iter_synsets(thai_path):
        thai_synset_source_count += 1
        if synset["ili"]:
            thai_ilis.add(synset["ili"])
        else:
            thai_synsets_without_ili += 1

    english_by_ili: dict[str, str] = {}
    aligned_ids: set[str] = set()
    context_ids: set[str] = set()
    for synset in parser.iter_synsets(english_path):
        if synset["ili"] not in thai_ilis:
            continue
        english_by_ili[synset["ili"]] = synset["id"]
        aligned_ids.add(synset["id"])
        context_ids.update(
            relation["target"]
            for relation in synset["relations"]
            if relation["type"] in SELECTED_SYNSET_RELATIONS
        )
    selected_english_ids = aligned_ids | context_ids

    lemmas_by_english_synset: dict[str, list[str]] = {}
    for entry in parser.iter_entries(english_path, synset_filter=selected_english_ids):
        for sense in entry["senses"]:
            lemmas = lemmas_by_english_synset.setdefault(sense["synset_id"], [])
            if entry["lemma"] not in lemmas:
                lemmas.append(entry["lemma"])

    for path in (
        thai_normalized_path,
        english_normalized_path,
        thai_rdf_path,
        english_rdf_path,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)

    thai_source_entry_count = 0
    thai_sense_count = 0
    thai_lemmas: set[str] = set()
    lemmas_by_thai_synset: dict[str, list[str]] = {}
    with thai_normalized_path.open("w", encoding="utf-8", newline="\n") as normalized, thai_rdf_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as rdf:
        _write_jsonl_record(normalized, "metadata", thai_metadata)
        batch: list[dict[str, Any]] = []
        for entry in parser.iter_entries(thai_path):
            _write_jsonl_record(normalized, "entry", entry)
            thai_source_entry_count += 1
            thai_sense_count += len(entry["senses"])
            thai_lemmas.add(entry["lemma"])
            for sense in entry["senses"]:
                lemmas = lemmas_by_thai_synset.setdefault(sense["synset_id"], [])
                if entry["lemma"] not in lemmas:
                    lemmas.append(entry["lemma"])
            batch.append(entry)
            if len(batch) >= batch_size:
                rdf.write(thai_to_turtle({"metadata": thai_metadata, "entries": batch, "synsets": []}))
                batch.clear()
        if batch:
            rdf.write(thai_to_turtle({"metadata": thai_metadata, "entries": batch, "synsets": []}))

        thai_synset_count = 0
        thai_missing_english_count = 0
        thai_missing_english_sample: list[str] = []
        batch = []
        for synset in parser.iter_synsets(thai_path):
            synset["lemmas"] = lemmas_by_thai_synset.get(synset["id"], [])
            synset["english_synset_id"] = english_by_ili.get(synset["ili"])
            if not synset["english_synset_id"]:
                thai_missing_english_count += 1
                if len(thai_missing_english_sample) < 20:
                    thai_missing_english_sample.append(synset["id"])
            _write_jsonl_record(normalized, "synset", synset)
            thai_synset_count += 1
            batch.append(synset)
            if len(batch) >= batch_size:
                rdf.write(thai_to_turtle({"metadata": thai_metadata, "entries": [], "synsets": batch}))
                batch.clear()
        if batch:
            rdf.write(thai_to_turtle({"metadata": thai_metadata, "entries": [], "synsets": batch}))

    english_synset_count = 0
    english_aligned_count = 0
    english_definition_count = 0
    english_example_count = 0
    relation_counts: Counter[str] = Counter()
    found_english_ids: set[str] = set()
    with english_normalized_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as normalized, english_rdf_path.open("w", encoding="utf-8", newline="\n") as rdf:
        _write_jsonl_record(normalized, "metadata", english_metadata)
        batch: list[dict[str, Any]] = []
        for synset in parser.iter_synsets(english_path, id_filter=selected_english_ids):
            synset["lemmas"] = lemmas_by_english_synset.get(synset["id"], [])
            _write_jsonl_record(normalized, "synset", synset)
            found_english_ids.add(synset["id"])
            english_synset_count += 1
            if synset["id"] in aligned_ids:
                english_aligned_count += 1
                english_definition_count += len(synset["definitions"])
                english_example_count += len(synset["examples"])
                relation_counts.update(
                    relation["type"]
                    for relation in synset["relations"]
                    if relation["type"] in SELECTED_SYNSET_RELATIONS
                )
            batch.append(synset)
            if len(batch) >= batch_size:
                batch_aligned = [
                    synset["id"] for synset in batch if synset["id"] in aligned_ids
                ]
                rdf.write(
                    english_to_turtle(
                        {
                            "metadata": english_metadata,
                            "aligned_synset_ids": batch_aligned,
                            "synsets": batch,
                        }
                    )
                )
                batch.clear()
        if batch:
            batch_aligned = [
                synset["id"] for synset in batch if synset["id"] in aligned_ids
            ]
            rdf.write(
                english_to_turtle(
                    {
                        "metadata": english_metadata,
                        "aligned_synset_ids": batch_aligned,
                        "synsets": batch,
                    }
                )
            )

    missing_english_targets = sorted(selected_english_ids - found_english_ids)
    elapsed_seconds = round(time.perf_counter() - started, 3)
    status = (
        "warning"
        if thai_synsets_without_ili
        or thai_missing_english_count
        or missing_english_targets
        else "passed"
    )
    return {
        "release": OMW_RELEASE,
        "selection_mode": "full",
        "thai_entry_count": len(thai_lemmas),
        "thai_source_entry_count": thai_source_entry_count,
        "thai_sense_count": thai_sense_count,
        "thai_synset_count": thai_synset_count,
        "thai_source_synset_count": thai_synset_source_count,
        "thai_synsets_without_ili_count": thai_synsets_without_ili,
        "aligned_english_synset_count": english_aligned_count,
        "thai_synsets_without_english_match_count": thai_missing_english_count,
        "thai_synsets_without_english_match_sample": thai_missing_english_sample,
        "english_synset_count": english_synset_count,
        "english_context_synset_count": len(found_english_ids - aligned_ids),
        "english_definition_count": english_definition_count,
        "english_example_count": english_example_count,
        "selected_relation_counts": dict(sorted(relation_counts.items())),
        "selected_relation_types": sorted(SELECTED_SYNSET_RELATIONS),
        "missing_english_relation_target_count": len(missing_english_targets),
        "missing_english_relation_target_sample": missing_english_targets[:20],
        "graphs": {
            "thai": THAI_GRAPH_IRI,
            "english": ENGLISH_GRAPH_IRI,
        },
        "outputs": {
            "thai_normalized_bytes": thai_normalized_path.stat().st_size,
            "english_normalized_bytes": english_normalized_path.stat().st_size,
            "thai_rdf_bytes": thai_rdf_path.stat().st_size,
            "english_rdf_bytes": english_rdf_path.stat().st_size,
        },
        "build_elapsed_seconds": elapsed_seconds,
        "status": status,
    }


def _dataset_turtle(metadata: dict[str, Any], source_id: str) -> list[str]:
    dataset = f"https://w3id.org/thailex/source/{source_id}/dataset"
    edition = f"https://w3id.org/thailex/edition/{source_id}-{metadata['version']}"
    predicates = [
        "a prov:Entity",
        f"dcterms:title {_literal(metadata['label'])}@{metadata['language']}",
        f"dcterms:source <{OMW_PROJECT_URL}>",
        f"dcterms:rights {_literal(metadata['license_text'])}",
        f"dcterms:bibliographicCitation {_literal(metadata['citation'])}",
        f"dcterms:hasVersion {_literal(metadata['version'])}",
    ]
    if metadata["license"].startswith(("http://", "https://")):
        predicates.append(f"dcterms:license <{metadata['license']}>")
    else:
        predicates.append(f"dcterms:license {_literal(metadata['license'])}")
    return [
        f"<{dataset}> " + " ;\n    ".join(predicates) + " .",
        f"<{edition}> a tlkg:DictionaryEdition ;",
        f"    dcterms:title {_literal(metadata['label'] + ' ' + metadata['version'])}@en ;",
        f"    prov:wasDerivedFrom <{dataset}> .",
        "",
    ]


def _prefixes() -> list[str]:
    return [
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix lexinfo: <https://lexinfo.net/ontology/3.0/lexinfo#> .",
        "@prefix ontolex: <http://www.w3.org/ns/lemon/ontolex#> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix tlkg: <https://w3id.org/thailex/ontology/> .",
        "",
    ]


def thai_to_turtle(data: dict[str, Any]) -> str:
    metadata = data["metadata"]
    lines = _prefixes() + _dataset_turtle(metadata, "omw-th")
    dataset = "https://w3id.org/thailex/source/omw-th/dataset"
    edition = f"https://w3id.org/thailex/edition/omw-th-{metadata['version']}"
    synset_by_id = {synset["id"]: synset for synset in data["synsets"]}

    for entry in data["entries"]:
        entry_uri = f"https://w3id.org/thailex/entry/th/{_iri_segment(entry['lemma'])}"
        form_uri = f"https://w3id.org/thailex/form/th/{_iri_segment(entry['lemma'])}"
        sense_uris = [
            f"https://w3id.org/thailex/sense/omw-th/{_iri_segment(sense['id'])}"
            for sense in entry["senses"]
        ]
        predicates = [
            "a ontolex:LexicalEntry",
            f"rdfs:label {_literal(entry['lemma'])}@th",
            f"ontolex:canonicalForm <{form_uri}>",
            f"tlkg:sourceRecordId {_literal(entry['id'])}",
            f"prov:wasDerivedFrom <{dataset}>",
        ] + [f"ontolex:sense <{sense_uri}>" for sense_uri in sense_uris]
        lexinfo_pos = LEXINFO_POS.get(entry["pos"])
        if lexinfo_pos:
            predicates.append(f"lexinfo:partOfSpeech lexinfo:{lexinfo_pos}")
        lines.extend(
            [
                f"<{entry_uri}> " + " ;\n    ".join(predicates) + " .",
                f"<{form_uri}> a ontolex:Form ;",
                f"    ontolex:writtenRep {_literal(entry['lemma'])}@th .",
            ]
        )
        for sense in entry["senses"]:
            sense_uri = f"https://w3id.org/thailex/sense/omw-th/{_iri_segment(sense['id'])}"
            synset_uri = f"https://w3id.org/thailex/concept/omw-th/{_iri_segment(sense['synset_id'])}"
            sense_predicates = [
                "a ontolex:LexicalSense",
                f"ontolex:isSenseOf <{entry_uri}>",
                f"ontolex:reference <{synset_uri}>",
                f"tlkg:sourceRecordId {_literal(sense['id'])}",
                f"tlkg:originalPartOfSpeech {_literal(entry['source_pos'])}",
                f"tlkg:inEdition <{edition}>",
                f"prov:wasDerivedFrom <{dataset}>",
            ]
            if lexinfo_pos:
                sense_predicates.append(f"lexinfo:partOfSpeech lexinfo:{lexinfo_pos}")
            lines.append(f"<{sense_uri}> " + " ;\n    ".join(sense_predicates) + " .")

    for synset in data["synsets"]:
        synset_uri = f"https://w3id.org/thailex/concept/omw-th/{_iri_segment(synset['id'])}"
        predicates = [
            "a ontolex:LexicalConcept, skos:Concept",
            f"tlkg:sourceRecordId {_literal(synset['id'])}",
            f"prov:wasDerivedFrom <{dataset}>",
        ]
        for index, lemma in enumerate(synset["lemmas"]):
            predicate = "skos:prefLabel" if index == 0 else "skos:altLabel"
            predicates.append(f"{predicate} {_literal(lemma)}@th")
        if synset["ili"]:
            ili_uri = f"https://w3id.org/thailex/ili/{_iri_segment(synset['ili'])}"
            predicates.append(f"tlkg:interlingualIndex <{ili_uri}>")
        if synset.get("english_synset_id"):
            english_uri = (
                "https://w3id.org/thailex/concept/omw-en/"
                + _iri_segment(synset["english_synset_id"])
            )
            predicates.append(f"skos:exactMatch <{english_uri}>")
        lines.append(f"<{synset_uri}> " + " ;\n    ".join(predicates) + " .")
        if synset["ili"]:
            lines.extend(
                [
                    f"<{ili_uri}> a tlkg:InterlingualIndex ;",
                    f"    dcterms:identifier {_literal(synset['ili'])} .",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


RELATION_PREDICATES = {
    "hypernym": "tlkg:hypernym",
    "instance_hypernym": "tlkg:hypernym",
    "hyponym": "tlkg:hyponym",
    "instance_hyponym": "tlkg:hyponym",
    "similar": "tlkg:similar",
    "mero_member": "tlkg:meronym",
    "mero_part": "tlkg:meronym",
    "mero_substance": "tlkg:meronym",
    "holo_member": "tlkg:holonym",
    "holo_part": "tlkg:holonym",
    "holo_substance": "tlkg:holonym",
    "entails": "tlkg:entails",
    "causes": "tlkg:causes",
    "domain_topic": "tlkg:domainTopic",
    "attribute": "tlkg:attribute",
}


def english_to_turtle(data: dict[str, Any]) -> str:
    metadata = data["metadata"]
    lines = _prefixes() + _dataset_turtle(metadata, "omw-en")
    dataset = "https://w3id.org/thailex/source/omw-en/dataset"
    edition = f"https://w3id.org/thailex/edition/omw-en-{metadata['version']}"
    aligned_ids = set(data["aligned_synset_ids"])

    for synset in data["synsets"]:
        synset_uri = f"https://w3id.org/thailex/concept/omw-en/{_iri_segment(synset['id'])}"
        predicates = [
            "a ontolex:LexicalConcept, skos:Concept",
            f"tlkg:sourceRecordId {_literal(synset['id'])}",
            f"tlkg:inEdition <{edition}>",
            f"prov:wasDerivedFrom <{dataset}>",
        ]
        for index, lemma in enumerate(synset["lemmas"]):
            predicate = "skos:prefLabel" if index == 0 else "skos:altLabel"
            predicates.append(f"{predicate} {_literal(lemma)}@en")
        if synset["ili"]:
            ili_uri = f"https://w3id.org/thailex/ili/{_iri_segment(synset['ili'])}"
            predicates.append(f"tlkg:interlingualIndex <{ili_uri}>")
        if synset["id"] in aligned_ids:
            for index, _ in enumerate(synset["definitions"], start=1):
                definition_uri = (
                    f"https://w3id.org/thailex/definition/omw-en/"
                    f"{_iri_segment(synset['id'])}/{index}"
                )
                predicates.append(f"tlkg:hasSynsetDefinition <{definition_uri}>")
            for example in synset["examples"]:
                predicates.append(f"tlkg:synsetExampleText {_literal(example)}@en")
            for relation in synset["relations"]:
                predicate = RELATION_PREDICATES.get(relation["type"])
                if predicate:
                    target_uri = (
                        "https://w3id.org/thailex/concept/omw-en/"
                        + _iri_segment(relation["target"])
                    )
                    predicates.append(f"{predicate} <{target_uri}>")
        lines.append(f"<{synset_uri}> " + " ;\n    ".join(predicates) + " .")
        if synset["ili"]:
            lines.extend(
                [
                    f"<{ili_uri}> a tlkg:InterlingualIndex ;",
                    f"    dcterms:identifier {_literal(synset['ili'])} .",
                ]
            )

        if synset["id"] not in aligned_ids:
            continue
        for index, definition in enumerate(synset["definitions"], start=1):
            definition_uri = (
                f"https://w3id.org/thailex/definition/omw-en/"
                f"{_iri_segment(synset['id'])}/{index}"
            )
            lines.extend(
                [
                    f"<{definition_uri}> a tlkg:DefinitionAssertion ;",
                    f"    tlkg:definitionText {_literal(definition)}@en ;",
                    f"    prov:wasDerivedFrom <{dataset}> ;",
                    "    tlkg:assertionStatus tlkg:SourceFactStatus ;",
                    f"    tlkg:inEdition <{edition}> .",
                ]
            )
        for relation in synset["relations"]:
            predicate = RELATION_PREDICATES.get(relation["type"])
            if not predicate:
                continue
            target_uri = (
                "https://w3id.org/thailex/concept/omw-en/"
                + _iri_segment(relation["target"])
            )
            assertion_uri = (
                "https://w3id.org/thailex/relation/omw-en/"
                + _relation_id(synset["id"], relation["type"], relation["target"])
            )
            lines.extend(
                [
                    f"<{assertion_uri}> a tlkg:RelationAssertion ;",
                    f"    rdf:subject <{synset_uri}> ;",
                    f"    rdf:predicate {predicate} ;",
                    f"    rdf:object <{target_uri}> ;",
                    f"    tlkg:sourceRelationType {_literal(relation['type'])} ;",
                    f"    prov:wasDerivedFrom <{dataset}> ;",
                    "    tlkg:assertionStatus tlkg:SourceFactStatus .",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
