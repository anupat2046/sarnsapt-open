from __future__ import annotations

import argparse
import json
from pathlib import Path

from .lexitron import (
    LexitronAdapter,
    build_lexitron_subset,
    load_policy,
    records_to_turtle,
    write_fixture,
    write_json,
    write_jsonl,
)
from .wordnet_lmf import (
    OMW_RELEASE,
    WordNetLMFParser,
    build_omw_full_streaming,
    build_omw_subset,
    english_to_turtle,
    thai_to_turtle,
    write_json as write_omw_json,
)
from .wiktextract import (
    WiktextractAdapter,
    build_wiktextract,
    records_to_turtle as wiktextract_to_turtle,
    write_json as write_wiktextract_json,
    write_jsonl as write_wiktextract_jsonl,
)
from .organizer import (
    audit_dataset as audit_organizer_dataset,
    build_dataset as build_organizer_dataset,
    records_to_turtle as organizer_to_turtle,
    write_json as write_organizer_json,
    write_jsonl as write_organizer_jsonl,
)
from .alignment import (
    build_alignment,
    candidates_to_turtle,
    select_review_batch,
    write_json as write_alignment_json,
    write_jsonl as write_alignment_jsonl,
    write_review_csv,
)


def _lemmas_from_file(path: Path | None) -> list[str] | None:
    if path is None:
        return None
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _audit(args: argparse.Namespace) -> int:
    adapter = LexitronAdapter(load_policy(args.policy))
    report = adapter.audit(args.input)
    write_json(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "rows": report["row_count"],
                "unique_lemmas": report["unique_lemma_count"],
                "encoding": report["file"]["encoding"],
                "importable_records": report["data_policy"]["importable_record_count"],
                "rejected_records": report["data_policy"]["rejected_record_count"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "failed" else 0


def _build(args: argparse.Namespace) -> int:
    adapter = LexitronAdapter(load_policy(args.policy))
    headers, rows, _, _ = adapter.read_rows(args.input)
    lemmas = _lemmas_from_file(args.lemma_file)
    records, selected_rows, report = build_lexitron_subset(
        adapter,
        rows,
        requested_lemmas=lemmas,
        limit_lemmas=(len(rows) if args.all_records else args.limit_lemmas),
    )
    write_jsonl(args.normalized_output, records)
    args.rdf_output.parent.mkdir(parents=True, exist_ok=True)
    args.rdf_output.write_text(records_to_turtle(records), encoding="utf-8", newline="\n")
    write_json(args.validation_output, report)
    if args.fixture_output:
        write_fixture(args.fixture_output, headers, selected_rows)
    print(
        json.dumps(
            {
                "status": report["status"],
                "valid_records": report["valid_record_count"],
                "definition_bearing": report["definition_bearing_count"],
                "translation_only": report["translation_only_count"],
                "unique_lemmas": report["unique_lemma_count"],
                "normalized_output": str(args.normalized_output),
                "rdf_output": str(args.rdf_output),
                "validation_output": str(args.validation_output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "failed" else 0


def _audit_omw(args: argparse.Namespace) -> int:
    parser = WordNetLMFParser()
    report = {
        "release": OMW_RELEASE,
        "thai": parser.audit(args.thai_input),
        "english": parser.audit(args.english_input),
        "status": "passed",
    }
    write_omw_json(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "thai_entries": report["thai"]["counts"]["lexical_entries"],
                "thai_senses": report["thai"]["counts"]["senses"],
                "english_synsets": report["english"]["counts"]["synsets"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _build_omw(args: argparse.Namespace) -> int:
    parser = WordNetLMFParser()
    lemmas = _lemmas_from_file(args.lemma_file) or []
    thai_data, english_data, report = build_omw_subset(
        parser, args.thai_input, args.english_input, lemmas
    )
    write_omw_json(args.thai_normalized_output, thai_data)
    write_omw_json(args.english_normalized_output, english_data)
    args.thai_rdf_output.parent.mkdir(parents=True, exist_ok=True)
    args.thai_rdf_output.write_text(thai_to_turtle(thai_data), encoding="utf-8", newline="\n")
    args.english_rdf_output.parent.mkdir(parents=True, exist_ok=True)
    args.english_rdf_output.write_text(
        english_to_turtle(english_data), encoding="utf-8", newline="\n"
    )
    write_omw_json(args.validation_output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "matched_lemmas": report["matched_lemma_count"],
                "thai_canonical_entries": report["thai_entry_count"],
                "thai_source_entries": report["thai_source_entry_count"],
                "thai_senses": report["thai_sense_count"],
                "aligned_english_synsets": report["aligned_english_synset_count"],
                "english_definitions": report["english_definition_count"],
                "validation_output": str(args.validation_output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "failed" else 0


def _build_omw_full(args: argparse.Namespace) -> int:
    parser = WordNetLMFParser()
    report = build_omw_full_streaming(
        parser,
        args.thai_input,
        args.english_input,
        args.thai_normalized_output,
        args.english_normalized_output,
        args.thai_rdf_output,
        args.english_rdf_output,
        batch_size=args.batch_size,
    )
    write_omw_json(args.validation_output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "thai_canonical_entries": report["thai_entry_count"],
                "thai_source_entries": report["thai_source_entry_count"],
                "thai_senses": report["thai_sense_count"],
                "aligned_english_synsets": report["aligned_english_synset_count"],
                "english_synsets": report["english_synset_count"],
                "english_definitions": report["english_definition_count"],
                "build_seconds": report["build_elapsed_seconds"],
                "validation_output": str(args.validation_output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "failed" else 0


def _audit_wiktextract(args: argparse.Namespace) -> int:
    adapter = WiktextractAdapter(args.edition)
    report = adapter.audit(args.input)
    write_wiktextract_json(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "thai_entries": report["counts"].get("thai_entry_records", 0),
                "senses": report["counts"].get("senses", 0),
                "sense_relations": sum(report["sense_relation_counts"].values()),
                "skipped_ambiguous_relations": sum(
                    report["skipped_ambiguous_relation_counts"].values()
                ),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "failed" else 0


def _build_wiktextract(args: argparse.Namespace) -> int:
    adapter = WiktextractAdapter(args.edition)
    lemmas = _lemmas_from_file(args.lemma_file)
    records, report = build_wiktextract(adapter, args.input, lemmas)
    write_wiktextract_jsonl(args.normalized_output, records)
    args.rdf_output.parent.mkdir(parents=True, exist_ok=True)
    args.rdf_output.write_text(
        wiktextract_to_turtle(records), encoding="utf-8", newline="\n"
    )
    write_wiktextract_json(args.validation_output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "selection_mode": report["selection_mode"],
                "entry_records": report["selected_entry_record_count"],
                "unique_lemmas": report["unique_lemma_count"],
                "sense_records": report["valid_sense_record_count"],
                "definitions": report["gloss_count"],
                "sense_relations": report["sense_relation_count"],
                "skipped_ambiguous_relations": report[
                    "skipped_ambiguous_relation_count"
                ],
                "validation_output": str(args.validation_output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "failed" else 0


def _audit_organizer(args: argparse.Namespace) -> int:
    report, _, _, _ = audit_organizer_dataset(args.input, args.mapping)
    write_organizer_json(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "format": report["input_file"]["format"],
                "source_records": report["counts"].get("source_records", 0),
                "importable_records": report["counts"].get("importable_records", 0),
                "rejected_records": report["counts"].get("rejected_records", 0),
                "graph_iri": report["graph_iri"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "failed" else 0


def _build_organizer(args: argparse.Namespace) -> int:
    records, report, mapping = build_organizer_dataset(
        args.input, args.mapping, args.audit_report
    )
    write_organizer_jsonl(args.normalized_output, records)
    args.rdf_output.parent.mkdir(parents=True, exist_ok=True)
    args.rdf_output.write_text(
        organizer_to_turtle(records, mapping), encoding="utf-8", newline="\n"
    )
    write_organizer_json(args.validation_output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "valid_records": report["valid_record_count"],
                "invalid_records": report["invalid_record_count"],
                "unique_lemmas": report["unique_lemma_count"],
                "definitions": report["definition_count"],
                "graph_iri": report["graph_iri"],
                "validation_output": str(args.validation_output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "failed" else 0


def _build_alignments(args: argparse.Namespace) -> int:
    lemmas = _lemmas_from_file(args.lemma_file) or []
    candidates, report = build_alignment(args.endpoint, lemmas, args.decisions)
    write_alignment_jsonl(args.candidates_output, candidates)
    write_review_csv(args.review_output, candidates)
    args.proposed_rdf_output.parent.mkdir(parents=True, exist_ok=True)
    args.proposed_rdf_output.write_text(
        candidates_to_turtle(candidates, "pending"), encoding="utf-8", newline="\n"
    )
    args.reviewed_rdf_output.parent.mkdir(parents=True, exist_ok=True)
    args.reviewed_rdf_output.write_text(
        candidates_to_turtle(candidates, "reviewed"), encoding="utf-8", newline="\n"
    )
    write_alignment_json(args.validation_output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "sources": report["source_count"],
                "senses": report["sense_count"],
                "candidates": report["candidate_count"],
                "pending": report["pending_count"],
                "approved": report["approved_count"],
                "rejected": report["rejected_count"],
                "review_output": str(args.review_output),
                "validation_output": str(args.validation_output),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "failed" else 0


def _select_alignment_review_batch(args: argparse.Namespace) -> int:
    candidates = [
        json.loads(line)
        for line in args.candidates_input.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    lemmas = _lemmas_from_file(args.lemma_file) or []
    selected, report = select_review_batch(
        candidates,
        lemmas,
        size=args.size,
        per_lemma=args.per_lemma,
        min_confidence=args.min_confidence,
    )
    payload = {
        "version": "1.0",
        "batch_id": args.batch_id,
        "summary": report,
        "candidates": selected,
    }
    write_alignment_json(args.output, payload)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ThaiLex dataset ingestion tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit-lexitron", help="Audit a LEXiTRON telex CSV")
    audit.add_argument("--input", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument("--policy", type=Path)
    audit.set_defaults(handler=_audit)

    build = subparsers.add_parser("build-lexitron", help="Normalize and convert a LEXiTRON subset")
    build.add_argument("--input", type=Path, required=True)
    build.add_argument("--policy", type=Path)
    build.add_argument("--lemma-file", type=Path)
    build.add_argument("--limit-lemmas", type=int, default=50)
    build.add_argument(
        "--all-records",
        action="store_true",
        help="Apply the data policy to the complete CSV instead of a lemma subset",
    )
    build.add_argument("--normalized-output", type=Path, required=True)
    build.add_argument("--rdf-output", type=Path, required=True)
    build.add_argument("--validation-output", type=Path, required=True)
    build.add_argument("--fixture-output", type=Path)
    build.set_defaults(handler=_build)

    audit_omw = subparsers.add_parser(
        "audit-omw", help="Audit Thai and English OMW WN-LMF XML"
    )
    audit_omw.add_argument("--thai-input", type=Path, required=True)
    audit_omw.add_argument("--english-input", type=Path, required=True)
    audit_omw.add_argument("--output", type=Path, required=True)
    audit_omw.set_defaults(handler=_audit_omw)

    build_omw = subparsers.add_parser(
        "build-omw", help="Build an aligned Thai/English OMW prototype subset"
    )
    build_omw.add_argument("--thai-input", type=Path, required=True)
    build_omw.add_argument("--english-input", type=Path, required=True)
    build_omw.add_argument("--lemma-file", type=Path, required=True)
    build_omw.add_argument("--thai-normalized-output", type=Path, required=True)
    build_omw.add_argument("--english-normalized-output", type=Path, required=True)
    build_omw.add_argument("--thai-rdf-output", type=Path, required=True)
    build_omw.add_argument("--english-rdf-output", type=Path, required=True)
    build_omw.add_argument("--validation-output", type=Path, required=True)
    build_omw.set_defaults(handler=_build_omw)

    build_omw_full = subparsers.add_parser(
        "build-omw-full", help="Build full aligned OMW graphs using bounded memory"
    )
    build_omw_full.add_argument("--thai-input", type=Path, required=True)
    build_omw_full.add_argument("--english-input", type=Path, required=True)
    build_omw_full.add_argument("--thai-normalized-output", type=Path, required=True)
    build_omw_full.add_argument("--english-normalized-output", type=Path, required=True)
    build_omw_full.add_argument("--thai-rdf-output", type=Path, required=True)
    build_omw_full.add_argument("--english-rdf-output", type=Path, required=True)
    build_omw_full.add_argument("--validation-output", type=Path, required=True)
    build_omw_full.add_argument("--batch-size", type=int, default=1000)
    build_omw_full.set_defaults(handler=_build_omw_full)

    audit_wiktextract = subparsers.add_parser(
        "audit-wiktextract", help="Audit Kaikki/Wiktextract Thai JSONL"
    )
    audit_wiktextract.add_argument("--input", type=Path, required=True)
    audit_wiktextract.add_argument("--edition", choices=("en", "th"), default="en")
    audit_wiktextract.add_argument("--output", type=Path, required=True)
    audit_wiktextract.set_defaults(handler=_audit_wiktextract)

    build_wiktextract_parser = subparsers.add_parser(
        "build-wiktextract",
        help="Normalize and convert Thai Wiktionary data from Wiktextract JSONL",
    )
    build_wiktextract_parser.add_argument("--input", type=Path, required=True)
    build_wiktextract_parser.add_argument(
        "--edition", choices=("en", "th"), default="en"
    )
    build_wiktextract_parser.add_argument("--lemma-file", type=Path)
    build_wiktextract_parser.add_argument(
        "--normalized-output", type=Path, required=True
    )
    build_wiktextract_parser.add_argument("--rdf-output", type=Path, required=True)
    build_wiktextract_parser.add_argument(
        "--validation-output", type=Path, required=True
    )
    build_wiktextract_parser.set_defaults(handler=_build_wiktextract)

    audit_organizer = subparsers.add_parser(
        "audit-organizer",
        help="Audit an organizer dataset using an explicit mapping config",
    )
    audit_organizer.add_argument("--input", type=Path, required=True)
    audit_organizer.add_argument("--mapping", type=Path, required=True)
    audit_organizer.add_argument("--output", type=Path, required=True)
    audit_organizer.set_defaults(handler=_audit_organizer)

    build_organizer = subparsers.add_parser(
        "build-organizer",
        help="Normalize an audited organizer dataset and generate RDF",
    )
    build_organizer.add_argument("--input", type=Path, required=True)
    build_organizer.add_argument("--mapping", type=Path, required=True)
    build_organizer.add_argument("--audit-report", type=Path, required=True)
    build_organizer.add_argument("--normalized-output", type=Path, required=True)
    build_organizer.add_argument("--rdf-output", type=Path, required=True)
    build_organizer.add_argument("--validation-output", type=Path, required=True)
    build_organizer.set_defaults(handler=_build_organizer)

    build_alignments = subparsers.add_parser(
        "build-alignments",
        help="Generate and review cross-source sense alignment candidates",
    )
    build_alignments.add_argument("--endpoint", required=True)
    build_alignments.add_argument("--lemma-file", type=Path, required=True)
    build_alignments.add_argument("--decisions", type=Path)
    build_alignments.add_argument("--candidates-output", type=Path, required=True)
    build_alignments.add_argument("--review-output", type=Path, required=True)
    build_alignments.add_argument("--proposed-rdf-output", type=Path, required=True)
    build_alignments.add_argument("--reviewed-rdf-output", type=Path, required=True)
    build_alignments.add_argument("--validation-output", type=Path, required=True)
    build_alignments.set_defaults(handler=_build_alignments)

    select_batch = subparsers.add_parser(
        "select-alignment-review-batch",
        help="Select a balanced high-confidence human review batch",
    )
    select_batch.add_argument("--candidates-input", type=Path, required=True)
    select_batch.add_argument("--lemma-file", type=Path, required=True)
    select_batch.add_argument("--output", type=Path, required=True)
    select_batch.add_argument("--batch-id", required=True)
    select_batch.add_argument("--size", type=int, default=40)
    select_batch.add_argument("--per-lemma", type=int, default=2)
    select_batch.add_argument("--min-confidence", type=float, default=0.85)
    select_batch.set_defaults(handler=_select_alignment_review_batch)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
