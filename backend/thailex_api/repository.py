from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from collections import OrderedDict
from urllib.parse import unquote, urlparse

from .models import (
    AlignmentCandidate,
    EvidenceItem,
    GraphEdge,
    GraphNode,
    GraphResult,
    ReviewCandidate,
    ReviewDecisionRequest,
    SearchItem,
    SenseCandidate,
    SenseLanguageDetails,
    LanguageDetailValue,
    ExampleDetail,
    SenseRelationDetail,
)
from .query_templates import QueryTemplates, sparql_int, sparql_iri, sparql_literal
from .sparql import SparqlClient


GRAPH_BASE = "https://w3id.org/thailex/graph/"


def source_from_graph(graph: str) -> str:
    if graph == f"{GRAPH_BASE}lexitron":
        return "lexitron"
    if graph == f"{GRAPH_BASE}thai-wordnet":
        return "thai-wordnet"
    if graph == f"{GRAPH_BASE}omw-en":
        return "omw-en"
    if graph == f"{GRAPH_BASE}wiktionary":
        return "wiktionary"
    if graph == f"{GRAPH_BASE}en-wiktionary-thai-entries":
        return "en-wiktionary-thai-entries"
    if graph == f"{GRAPH_BASE}th-wiktionary":
        return "th-wiktionary"
    if graph.startswith(f"{GRAPH_BASE}organizer/"):
        return "organizer"
    if graph.startswith(f"{GRAPH_BASE}demo-"):
        return "demo"
    return graph.removeprefix(GRAPH_BASE) or "unknown"


def _value(row: dict[str, dict[str, str]], key: str) -> str | None:
    return row.get(key, {}).get("value")


def _language(row: dict[str, dict[str, str]], key: str) -> str | None:
    binding = row.get(key, {})
    return binding.get("xml:lang") or binding.get("lang")


def _iri_value(row: dict[str, dict[str, str]], key: str) -> str | None:
    binding = row.get(key, {})
    return binding.get("value") if binding.get("type") in {"uri", "iri"} else None


def _license_value(
    row: dict[str, dict[str, str]], license_key: str, rights_key: str
) -> str | None:
    return _value(row, license_key) or (
        _value(row, rights_key) if not _iri_value(row, rights_key) else None
    )


def _local_name(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.fragment:
        return unquote(parsed.fragment)
    path = unquote(parsed.path.rstrip("/"))
    return path.rsplit("/", 1)[-1] or uri


def _evidence_id(sense_uri: str, kind: str, evidence_uri: str, text: str) -> str:
    digest = hashlib.sha256(
        "\x1f".join((sense_uri, kind, evidence_uri, text)).encode("utf-8")
    ).hexdigest()[:16]
    return f"ev-{digest}"


def _review_status(uri: str | None) -> str:
    tail = _local_name(uri or "")
    return {
        "ApprovedReviewStatus": "approved",
        "RejectedReviewStatus": "rejected",
    }.get(tail, "pending")


_RELATION_NAMES = {
    "synonym", "antonym", "hypernym", "hyponym", "similar",
    "holonym", "meronym", "entails", "causes", "domainTopic",
    "attribute", "coordinateTerm", "derivedTerm", "relatedTerm",
}


def _details_from_rows(
    rows: list[dict[str, dict[str, str]]],
    candidate: SenseCandidate,
    relation_rows: list[tuple[str, str, str, str]],
) -> SenseLanguageDetails:
    """Build details from sense_details rows and (relation, term, target, graph) tuples."""
    values: dict[str, list[LanguageDetailValue]] = {
        "translation": [],
        "pronunciation": [],
        "form": [],
        "romanization": [],
        "etymology": [],
    }
    value_index: dict[tuple[str, str, str | None], LanguageDetailValue] = {}
    examples: list[ExampleDetail] = []
    example_seen: set[tuple[str, str | None, str | None, str | None]] = set()
    quality_flags: list[str] = []
    source_notes: list[str] = []
    sense_number: str | None = None
    for row in rows:
        kind = _value(row, "kind")
        value = _value(row, "value")
        source_graph = _value(row, "sourceGraph") or candidate.source_graph
        if not kind or value is None:
            continue
        if kind == "sense-number":
            sense_number = sense_number or value
        elif kind == "quality-flag":
            if value not in quality_flags:
                quality_flags.append(value)
        elif kind == "source-note":
            if value not in source_notes:
                source_notes.append(value)
        elif kind == "example":
            key = (
                value,
                _language(row, "value"),
                _value(row, "translation"),
                _value(row, "translationLanguage") or _language(row, "translation"),
            )
            if key not in example_seen:
                example_seen.add(key)
                examples.append(
                    ExampleDetail(
                        text=value,
                        language=_language(row, "value"),
                        translation=_value(row, "translation"),
                        translation_language=(
                            _value(row, "translationLanguage")
                            or _language(row, "translation")
                        ),
                        uri=_value(row, "resource"),
                        source_graph=source_graph,
                    )
                )
        elif kind in values:
            key = (kind, value, _language(row, "value"))
            tag = _value(row, "tag")
            existing = value_index.get(key)
            if existing is None:
                detail_value = LanguageDetailValue(
                    value=value,
                    language=_language(row, "value"),
                    uri=_value(row, "resource"),
                    source_graph=source_graph,
                    tags=[tag] if tag else [],
                )
                value_index[key] = detail_value
                values[kind].append(detail_value)
            elif tag and tag not in existing.tags:
                existing.tags.append(tag)

    relations: list[SenseRelationDetail] = []
    relation_seen: set[tuple[str, str]] = set()
    for relation, term, target, source_graph in relation_rows:
        key = (relation, target)
        if relation not in _RELATION_NAMES or key in relation_seen:
            continue
        relation_seen.add(key)
        relations.append(
            SenseRelationDetail(
                relation=relation,
                term=term,
                target_uri=target,
                language="th" if any("\u0e00" <= char <= "\u0e7f" for char in term) else "en",
                source_graph=source_graph,
            )
        )
    return SenseLanguageDetails(
        sense_number=sense_number,
        translations=values["translation"],
        pronunciations=values["pronunciation"],
        forms=values["form"],
        romanizations=values["romanization"],
        etymologies=values["etymology"],
        examples=examples,
        relations=relations,
        quality_flags=quality_flags,
        source_notes=source_notes,
        license=candidate.license,
        attribution=candidate.attribution,
    )


class GraphRepository:
    def __init__(self, client: SparqlClient, templates: QueryTemplates | None = None) -> None:
        self.client = client
        self.templates = templates or QueryTemplates()

    async def health(self) -> bool:
        return await self.client.ask(self.templates.render("health"))

    async def search(self, query: str, limit: int = 20) -> list[SearchItem]:
        rendered = self.templates.render(
            "search_lemmas",
            QUERY=sparql_literal(query.strip()),
            LIMIT=sparql_int(limit, maximum=100),
        )
        rows = await self.client.select(rendered)
        results: list[SearchItem] = []
        for row in rows:
            graphs = (_value(row, "sourceGraphs") or "").split("|")
            sources = sorted({source_from_graph(graph) for graph in graphs if graph})
            results.append(
                SearchItem(
                    lemma=_value(row, "lemma") or "",
                    sense_count=int(_value(row, "senseCount") or 0),
                    sources=sources,
                )
            )
        return results

    async def lemma_inventory(self) -> list[tuple[str, int]]:
        """Every lemma with its sense count, for the in-memory lexicon index."""
        rows = await self.client.select(self.templates.render("lemma_inventory"))
        return [
            (lemma, int(_value(row, "senseCount") or 0))
            for row in rows
            if (lemma := _value(row, "lemma"))
        ]

    async def get_senses(self, lemma: str, limit: int = 100) -> list[SenseCandidate]:
        rendered = self.templates.render(
            "sense_candidates",
            LEMMA=sparql_literal(lemma.strip()),
            SENSE_LIMIT=sparql_int(limit, maximum=500),
            ROW_LIMIT=sparql_int(20000, maximum=20000),
        )
        return self._map_candidates(await self.client.select(rendered))[:limit]

    async def get_sense(self, sense_uri: str) -> SenseCandidate | None:
        rendered = self.templates.render(
            "sense_by_uri", SENSE_IRI=sparql_iri(sense_uri)
        )
        candidates = self._map_candidates(await self.client.select(rendered))
        return candidates[0] if candidates else None

    async def get_sense_details(
        self, candidate: SenseCandidate, *, semantic_graph: GraphResult | None = None
    ) -> SenseLanguageDetails:
        rendered = self.templates.render(
            "sense_details", SENSE_IRI=sparql_iri(candidate.sense_uri)
        )
        rows = await self.client.select(rendered)
        graph = semantic_graph or await self.semantic_graph(
            candidate.sense_uri, max_edges=30
        )
        node_labels = {node.uri: node.label for node in graph.nodes}
        relations = [
            (_local_name(edge.predicate), node_labels.get(edge.target, _local_name(edge.target)),
             edge.target, edge.source_graph)
            for edge in graph.edges
        ]
        return _details_from_rows(rows, candidate, relations)

    async def get_sense_details_batch(
        self, candidates: list[SenseCandidate]
    ) -> dict[str, SenseLanguageDetails]:
        """Language details and relations for many senses in two queries."""
        if not candidates:
            return {}
        by_uri = {candidate.sense_uri: candidate for candidate in candidates}
        iris = " ".join(sparql_iri(uri) for uri in by_uri)
        detail_rows, relation_rows = await asyncio.gather(
            self.client.select(self.templates.render(
                "sense_details_batch", SENSE_IRIS=iris,
                LIMIT=sparql_int(min(20000, 200 * len(by_uri)), maximum=20000),
            )),
            self.client.select(self.templates.render(
                "sense_relations_batch", SENSE_IRIS=iris,
                LIMIT=sparql_int(min(20000, 100 * len(by_uri)), maximum=20000),
            )),
        )
        rows_by_sense: dict[str, list[dict[str, dict[str, str]]]] = {uri: [] for uri in by_uri}
        for row in detail_rows:
            sense = _value(row, "sense")
            if sense in rows_by_sense:
                rows_by_sense[sense].append(row)
        relations_by_sense: dict[str, list[tuple[str, str, str, str]]] = {uri: [] for uri in by_uri}
        for row in relation_rows:
            sense = _value(row, "sense")
            predicate = _value(row, "predicate")
            target = _value(row, "object")
            relation_graph = _value(row, "relationGraph")
            if sense not in relations_by_sense or not all((predicate, target, relation_graph)):
                continue
            relations_by_sense[sense].append((
                _local_name(predicate),
                _value(row, "objectLabel") or _local_name(target),
                target,
                relation_graph,
            ))
        return {
            uri: _details_from_rows(rows_by_sense[uri], candidate, relations_by_sense[uri])
            for uri, candidate in by_uri.items()
        }

    def _map_alignment(self, row: dict[str, dict[str, str]]) -> AlignmentCandidate | None:
        assertion = _value(row, "assertion")
        left = _value(row, "leftSense")
        right = _value(row, "rightSense")
        if not all((assertion, left, right)):
            return None
        recommendation = _value(row, "recommendedRelation") or "possiblySameSense"
        if recommendation not in {"exactMatch", "closeMatch", "possiblySameSense"}:
            recommendation = "possiblySameSense"
        decided = _value(row, "decidedRelation") or None
        if decided not in {"exactMatch", "closeMatch"}:
            decided = None
        return AlignmentCandidate(
            candidate_id=_local_name(assertion),
            assertion_uri=assertion,
            left_sense_uri=left,
            right_sense_uri=right,
            confidence=float(_value(row, "confidence") or 0),
            semantic_similarity=float(_value(row, "semanticSimilarity") or 0),
            pos_compatibility=float(_value(row, "posCompatibility") or 0),
            method=_value(row, "method") or "unknown",
            recommended_relation=recommendation,
            review_status=_review_status(_value(row, "reviewStatus")),
            decided_relation=decided,
            reviewer=_value(row, "reviewer"),
            review_note=_value(row, "reviewNote"),
        )

    async def get_alignments(
        self, lemma: str | None = None, *, limit: int = 100
    ) -> list[AlignmentCandidate]:
        # Binding the lemma up front lets GraphDB use its index; a FILTER on
        # STR(?normalizedLemma) scanned every proposal in the repository.
        lemma_values = (
            "VALUES ?normalizedLemma {{ {0}@th {0} }}".format(sparql_literal(lemma.strip()))
            if lemma and lemma.strip()
            else ""
        )
        rendered = self.templates.render(
            "alignment_candidates",
            LEMMA_VALUES=lemma_values,
            LIMIT=sparql_int(limit, maximum=500),
        )
        mapped = [self._map_alignment(row) for row in await self.client.select(rendered)]
        by_id: dict[str, AlignmentCandidate] = {}
        for candidate in mapped:
            if candidate is None:
                continue
            current = by_id.get(candidate.candidate_id)
            if current is None or (
                candidate.review_status != "pending", candidate.confidence
            ) > (
                current.review_status != "pending", current.confidence
            ):
                by_id[candidate.candidate_id] = candidate
        return list(by_id.values())

    async def get_review_queue(
        self, lemma: str | None = None, *, limit: int = 40
    ) -> list[ReviewCandidate]:
        alignments = await self.get_alignments(lemma, limit=limit)
        sense_uris = list(
            dict.fromkeys(
                uri
                for item in alignments
                for uri in (item.left_sense_uri, item.right_sense_uri)
            )
        )
        senses = await asyncio.gather(*(self.get_sense(uri) for uri in sense_uris))
        by_uri = {sense.sense_uri: sense for sense in senses if sense is not None}
        return [
            ReviewCandidate(
                alignment=item,
                left=by_uri[item.left_sense_uri],
                right=by_uri[item.right_sense_uri],
            )
            for item in alignments
            if item.left_sense_uri in by_uri and item.right_sense_uri in by_uri
        ]

    async def save_review_decision(
        self, decision: ReviewDecisionRequest
    ) -> AlignmentCandidate | None:
        if decision.reviewer.strip().casefold().startswith("ai-"):
            raise ValueError("ผู้ตรวจต้องเป็นมนุษย์ ไม่สามารถใช้รหัสที่ขึ้นต้นด้วย AI-")
        if decision.review_status == "approved" and decision.relation is None:
            raise ValueError("ผลอนุมัติต้องเลือก exactMatch หรือ closeMatch")
        if decision.review_status == "rejected" and decision.relation is not None:
            raise ValueError("ผลปฏิเสธต้องไม่มี relation")

        assertion_uri = f"https://w3id.org/thailex/alignment/assertion/{decision.candidate_id}"
        rendered = self.templates.render(
            "alignment_candidate_by_id", ASSERTION_IRI=sparql_iri(assertion_uri)
        )
        rows = await self.client.select(rendered)
        current = self._map_alignment(rows[0]) if rows else None
        if current is None:
            return None
        normalized_lemma = _value(rows[0], "normalizedLemma")
        if not normalized_lemma:
            raise ValueError("Alignment candidate is missing its normalized lemma")

        predicate = decision.relation or current.recommended_relation
        predicate_iri = (
            f"http://www.w3.org/2004/02/skos/core#{predicate}"
            if predicate in {"exactMatch", "closeMatch"}
            else "https://w3id.org/thailex/ontology/possiblySameSense"
        )
        status_iri = (
            "https://w3id.org/thailex/ontology/ApprovedReviewStatus"
            if decision.review_status == "approved"
            else "https://w3id.org/thailex/ontology/RejectedReviewStatus"
        )
        assertion_status_iri = (
            "https://w3id.org/thailex/ontology/ReviewedStatus"
            if decision.review_status == "approved"
            else "https://w3id.org/thailex/ontology/RejectedStatus"
        )
        direct_edge = (
            f"{sparql_iri(current.left_sense_uri)} {sparql_iri(predicate_iri)} "
            f"{sparql_iri(current.right_sense_uri)} ."
            if decision.review_status == "approved"
            else ""
        )
        update = f"""
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX tlkg: <https://w3id.org/thailex/ontology/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
DELETE {{
  GRAPH <https://w3id.org/thailex/graph/alignment/reviewed> {{
    {sparql_iri(assertion_uri)} ?oldPredicate ?oldObject .
    {sparql_iri(current.left_sense_uri)} ?oldRelation {sparql_iri(current.right_sense_uri)} .
  }}
}}
INSERT {{
  GRAPH <https://w3id.org/thailex/graph/alignment/reviewed> {{
    {direct_edge}
    {sparql_iri(assertion_uri)} a tlkg:AlignmentAssertion, rdf:Statement, prov:Entity ;
      rdf:subject {sparql_iri(current.left_sense_uri)} ;
      rdf:predicate {sparql_iri(predicate_iri)} ;
      rdf:object {sparql_iri(current.right_sense_uri)} ;
      tlkg:confidence {sparql_literal(str(current.confidence))}^^xsd:decimal ;
      tlkg:semanticSimilarity {sparql_literal(str(current.semantic_similarity))}^^xsd:decimal ;
      tlkg:posCompatibility {sparql_literal(str(current.pos_compatibility))}^^xsd:decimal ;
      tlkg:alignmentMethod {sparql_literal(current.method)} ;
      tlkg:normalizedLemma {sparql_literal(normalized_lemma)}@th ;
      tlkg:recommendedRelation {sparql_literal(current.recommended_relation)} ;
      tlkg:reviewStatus {sparql_iri(status_iri)} ;
      tlkg:assertionStatus {sparql_iri(assertion_status_iri)} ;
      tlkg:reviewedBy {sparql_literal(decision.reviewer.strip())} ;
      tlkg:reviewNote {sparql_literal(decision.note.strip())} ;
      prov:generatedAtTime {sparql_literal(datetime.now(UTC).isoformat())}^^xsd:dateTime .
  }}
}}
WHERE {{
  OPTIONAL {{
    GRAPH <https://w3id.org/thailex/graph/alignment/reviewed> {{
      {sparql_iri(assertion_uri)} ?oldPredicate ?oldObject .
    }}
  }}
  OPTIONAL {{
    GRAPH <https://w3id.org/thailex/graph/alignment/reviewed> {{
      {sparql_iri(current.left_sense_uri)} ?oldRelation {sparql_iri(current.right_sense_uri)} .
      FILTER(?oldRelation IN (
        <http://www.w3.org/2004/02/skos/core#exactMatch>,
        <http://www.w3.org/2004/02/skos/core#closeMatch>
      ))
    }}
  }}
}}
"""
        await self.client.update(update)
        refreshed_rows = await self.client.select(rendered)
        return self._map_alignment(refreshed_rows[0]) if refreshed_rows else None

    def _map_candidates(
        self, rows: list[dict[str, dict[str, str]]]
    ) -> list[SenseCandidate]:
        grouped: OrderedDict[str, dict[str, object]] = OrderedDict()
        evidence_seen: dict[str, set[str]] = {}
        for row in rows:
            sense_uri = _value(row, "sense")
            entry_uri = _value(row, "entry")
            source_graph = _value(row, "sourceGraph")
            lemma = _value(row, "lemma")
            if not all((sense_uri, entry_uri, source_graph, lemma)):
                continue
            if sense_uri not in grouped:
                pos_uri = _value(row, "pos")
                sense_source = source_from_graph(source_graph)
                grouped[sense_uri] = {
                    "sense_uri": sense_uri,
                    "entry_uri": entry_uri,
                    "concept_uri": _value(row, "concept"),
                    "lemma": lemma,
                    "pos": _local_name(pos_uri) if pos_uri else None,
                    "original_pos": _value(row, "originalPos"),
                    "register": _value(row, "register"),
                    "source": sense_source,
                    "source_graph": source_graph,
                    "source_record_id": _value(row, "sourceRecordId"),
                    "sense_source": sense_source,
                    "dataset": _value(row, "senseDatasetTitle"),
                    "edition": _value(row, "senseEdition"),
                    "edition_uri": _value(row, "senseEditionUri"),
                    "source_url": _value(row, "senseSourceUrl"),
                    "license": _license_value(row, "senseLicense", "senseRights"),
                    "attribution": _iri_value(row, "senseRights"),
                    "evidence": [],
                    "retrieval_score": 0.0,
                }
                evidence_seen[sense_uri] = set()
            kind = _value(row, "kind")
            text = _value(row, "text")
            evidence_uri = _value(row, "evidenceUri")
            evidence_graph = _value(row, "evidenceGraph") or source_graph
            evidence_language = _language(row, "text")
            if kind and text and evidence_uri and kind in {
                "definition", "example", "synset-definition", "synset-example"
            }:
                evidence_id = _evidence_id(sense_uri, kind, evidence_uri, text)
                if evidence_id not in evidence_seen[sense_uri]:
                    grouped[sense_uri]["evidence"].append(
                        EvidenceItem(
                            evidence_id=evidence_id,
                            kind=kind,
                            text=text,
                            language=evidence_language,
                            evidence_uri=evidence_uri,
                            source_graph=evidence_graph,
                            derived_from=_value(row, "derivedFrom"),
                            sense_source=source_from_graph(source_graph),
                            evidence_source=source_from_graph(evidence_graph),
                            evidence_language=evidence_language,
                            edition=_value(row, "evidenceEdition"),
                            edition_uri=_value(row, "evidenceEditionUri"),
                            source_url=_value(row, "evidenceSourceUrl"),
                            license=_license_value(row, "evidenceLicense", "evidenceRights"),
                            attribution=_iri_value(row, "evidenceRights"),
                        )
                    )
                    evidence_seen[sense_uri].add(evidence_id)
        return [SenseCandidate.model_validate(item) for item in grouped.values()]

    async def graph(
        self, start_uris: list[str], *, hops: int = 1, max_edges: int = 50
    ) -> GraphResult:
        if hops not in {1, 2}:
            raise ValueError("hops must be 1 or 2")
        edge_rows: list[GraphEdge] = []
        labels: dict[str, str] = {}
        node_graphs: dict[str, str] = {}
        seen_edges: set[tuple[str, str, str, str]] = set()
        frontier = list(dict.fromkeys(start_uris))
        visited: set[str] = set()
        truncated = False

        for hop in range(1, hops + 1):
            frontier = [uri for uri in frontier if uri not in visited]
            if not frontier or len(edge_rows) >= max_edges:
                break
            visited.update(frontier)
            room = max_edges - len(edge_rows)
            rendered = self.templates.render(
                "neighbors",
                START_IRIS=" ".join(sparql_iri(uri) for uri in frontier[:50]),
                LIMIT=sparql_int(min(room + 1, 200), maximum=200),
            )
            rows = await self.client.select(rendered)
            if len(rows) > room:
                truncated = True
                rows = rows[:room]
            next_frontier: list[str] = []
            for row in rows:
                subject = _value(row, "subject")
                predicate = _value(row, "predicate")
                target = _value(row, "object")
                source_graph = _value(row, "sourceGraph")
                if not all((subject, predicate, target, source_graph)):
                    continue
                key = (subject, predicate, target, source_graph)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edge_rows.append(
                    GraphEdge(
                        source=subject,
                        predicate=predicate,
                        target=target,
                        source_graph=source_graph,
                        hop=hop,
                    )
                )
                labels.setdefault(subject, _value(row, "subjectLabel") or _local_name(subject))
                labels.setdefault(target, _value(row, "objectLabel") or _local_name(target))
                node_graphs.setdefault(
                    subject, _value(row, "subjectLabelGraph") or source_graph
                )
                node_graphs.setdefault(
                    target, _value(row, "objectLabelGraph") or source_graph
                )
                next_frontier.append(target)
            frontier = next_frontier

        for uri in start_uris:
            labels.setdefault(uri, _local_name(uri))
        nodes = [
            GraphNode(
                uri=uri,
                label=label,
                source_graph=node_graphs.get(uri),
                kind=(
                    "sense"
                    if "/sense/" in uri
                    else "concept"
                    if "/concept/" in uri or "/synset/" in uri
                    else "resource"
                ),
            )
            for uri, label in sorted(labels.items())
        ]
        return GraphResult(hops=hops, nodes=nodes, edges=edge_rows, truncated=truncated)

    async def semantic_graph(
        self, sense_uri: str, *, max_edges: int = 50
    ) -> GraphResult:
        rendered = self.templates.render(
            "semantic_neighbors",
            SENSE_IRI=sparql_iri(sense_uri),
            LIMIT=sparql_int(min(max_edges + 1, 200), maximum=200),
        )
        rows = await self.client.select(rendered)
        truncated = len(rows) > max_edges
        rows = rows[:max_edges]
        labels: dict[str, str] = {sense_uri: _local_name(sense_uri)}
        node_graphs: dict[str, str] = {}
        edges: list[GraphEdge] = []
        seen: set[tuple[str, str, str, str]] = set()

        def add_edge(source: str, predicate: str, target: str, graph: str, hop: int) -> None:
            key = (source, predicate, target, graph)
            if key not in seen:
                seen.add(key)
                edges.append(
                    GraphEdge(
                        source=source,
                        predicate=predicate,
                        target=target,
                        source_graph=graph,
                        hop=hop,
                    )
                )

        for row in rows:
            concept = _value(row, "concept")
            sense_graph = _value(row, "senseGraph")
            predicate = _value(row, "predicate")
            target = _value(row, "object")
            relation_graph = _value(row, "relationGraph")
            if not all((concept, sense_graph, predicate, target, relation_graph)):
                continue
            labels[concept] = _value(row, "conceptLabel") or _local_name(concept)
            labels[target] = _value(row, "objectLabel") or _local_name(target)
            node_graphs.setdefault(sense_uri, sense_graph)
            node_graphs.setdefault(concept, sense_graph)
            node_graphs.setdefault(
                target, _value(row, "objectLabelGraph") or relation_graph
            )
            add_edge(
                sense_uri,
                "http://www.w3.org/ns/lemon/ontolex#reference",
                concept,
                sense_graph,
                1,
            )
            english = _value(row, "englishConcept")
            link_graph = _value(row, "linkGraph")
            if english and link_graph:
                labels[english] = _value(row, "englishLabel") or _local_name(english)
                add_edge(
                    concept,
                    "http://www.w3.org/2004/02/skos/core#exactMatch",
                    english,
                    link_graph,
                    2,
                )
                add_edge(english, predicate, target, relation_graph, 2)
            else:
                add_edge(concept, predicate, target, relation_graph, 2)

        if not edges:
            return await self.graph([sense_uri], hops=2, max_edges=max_edges)
        nodes = [
            GraphNode(
                uri=uri,
                label=label,
                source_graph=node_graphs.get(uri),
                kind=(
                    "sense" if "/sense/" in uri else
                    "concept" if "/concept/" in uri or "/synset/" in uri else
                    "resource"
                ),
            )
            for uri, label in labels.items()
        ]
        return GraphResult(hops=2, nodes=nodes, edges=edges, truncated=truncated)

    async def close(self) -> None:
        await self.client.close()
