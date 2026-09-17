from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceItem(ApiModel):
    evidence_id: str
    kind: Literal["definition", "example", "synset-definition", "synset-example"]
    text: str
    language: str | None = None
    evidence_uri: str
    source_graph: str
    derived_from: str | None = None
    sense_source: str | None = None
    evidence_source: str | None = None
    evidence_language: str | None = None
    edition: str | None = None
    edition_uri: str | None = None
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None


class SenseCandidate(ApiModel):
    sense_uri: str
    entry_uri: str
    concept_uri: str | None = None
    lemma: str
    pos: str | None = None
    original_pos: str | None = None
    register_label: str | None = Field(
        default=None, validation_alias="register", serialization_alias="register"
    )
    source: str
    source_graph: str
    source_record_id: str | None = None
    sense_source: str | None = None
    edition: str | None = None
    edition_uri: str | None = None
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    retrieval_score: float = Field(default=0.0, ge=0.0, le=1.0)


class SearchItem(ApiModel):
    lemma: str
    sense_count: int
    sources: list[str]


class SearchResponse(ApiModel):
    query: str
    results: list[SearchItem]


class SensesResponse(ApiModel):
    lemma: str
    candidates: list[SenseCandidate]
    evidence_validated: bool = True


class AlignmentCandidate(ApiModel):
    candidate_id: str
    assertion_uri: str
    left_sense_uri: str
    right_sense_uri: str
    confidence: float = Field(ge=0.0, le=1.0)
    semantic_similarity: float = Field(ge=0.0, le=1.0)
    pos_compatibility: float = Field(ge=0.0, le=1.0)
    method: str
    recommended_relation: Literal["exactMatch", "closeMatch", "possiblySameSense"]
    review_status: Literal["pending", "approved", "rejected"]
    decided_relation: Literal["exactMatch", "closeMatch"] | None = None
    reviewer: str | None = None
    review_note: str | None = None


class AlignmentsResponse(ApiModel):
    lemma: str | None = None
    candidates: list[AlignmentCandidate]


class ReviewCandidate(ApiModel):
    alignment: AlignmentCandidate
    left: SenseCandidate
    right: SenseCandidate


class ReviewQueueResponse(ApiModel):
    lemma: str | None = None
    candidates: list[ReviewCandidate]


class ReviewDecisionRequest(ApiModel):
    candidate_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    review_status: Literal["approved", "rejected"]
    relation: Literal["exactMatch", "closeMatch"] | None = None
    reviewer: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2000)


class ReviewDecisionResponse(ApiModel):
    saved: bool = True
    candidate: AlignmentCandidate


class GraphNode(ApiModel):
    uri: str
    label: str
    kind: Literal["sense", "concept", "resource"] = "resource"
    source_graph: str | None = None


class GraphEdge(ApiModel):
    source: str
    predicate: str
    target: str
    source_graph: str
    hop: int = Field(ge=1, le=2)


class GraphResult(ApiModel):
    hops: int = Field(ge=1, le=2)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    truncated: bool = False


class LanguageDetailValue(ApiModel):
    value: str
    language: str | None = None
    uri: str | None = None
    source_graph: str
    tags: list[str] = Field(default_factory=list)


class ExampleDetail(ApiModel):
    text: str
    language: str | None = None
    translation: str | None = None
    translation_language: str | None = None
    uri: str | None = None
    source_graph: str


class SenseRelationDetail(ApiModel):
    relation: str
    term: str
    target_uri: str
    language: str | None = None
    source_graph: str


class SenseLanguageDetails(ApiModel):
    sense_number: str | None = None
    translations: list[LanguageDetailValue] = Field(default_factory=list)
    pronunciations: list[LanguageDetailValue] = Field(default_factory=list)
    forms: list[LanguageDetailValue] = Field(default_factory=list)
    romanizations: list[LanguageDetailValue] = Field(default_factory=list)
    etymologies: list[LanguageDetailValue] = Field(default_factory=list)
    examples: list[ExampleDetail] = Field(default_factory=list)
    relations: list[SenseRelationDetail] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    source_notes: list[str] = Field(default_factory=list)
    license: str | None = None
    attribution: str | None = None


class SelectionDecision(ApiModel):
    selected_sense_uri: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    selector: Literal["heuristic", "openai", "thaillm", "none"]
    intent: Literal["define", "define_all", "related", "compare"] | None = None
    cue_words: list[str] = Field(default_factory=list)
    grounded_answer: str | None = None


class Citation(ApiModel):
    evidence_id: str
    kind: str
    text: str
    evidence_uri: str
    source: str
    source_graph: str
    derived_from: str | None = None
    sense_source: str | None = None
    evidence_source: str | None = None
    evidence_language: str | None = None
    edition: str | None = None
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None


class SourceSupport(ApiModel):
    source: str
    sense_uri: str
    pos: str | None = None
    definition: str | None = None
    example: str | None = None
    match_relation: Literal[
        "selected", "exactMatch", "closeMatch", "possiblySameSense", "unlinked"
    ]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    review_status: Literal["selected", "approved", "pending", "unlinked"]
    evidence_ids: list[str] = Field(default_factory=list)
    sense_source: str | None = None
    evidence_source: str | None = None
    evidence_language: str | None = None
    source_graph: str | None = None
    edition: str | None = None
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None


class RelatedTerm(ApiModel):
    term: str
    relation: str
    target_uri: str
    source_graph: str


class ReasoningStep(ApiModel):
    step: int = Field(ge=1)
    label: str
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)


class AnswerDetail(ApiModel):
    intent: Literal["define", "define_all", "related", "compare"]
    title: str
    summary: str
    explanation: str
    source_supports: list[SourceSupport] = Field(default_factory=list)
    related_terms: list[RelatedTerm] = Field(default_factory=list)
    reasoning_path: list[ReasoningStep] = Field(default_factory=list)


class AskDiagnostics(ApiModel):
    requested_provider: Literal["heuristic", "openai", "thaillm"]
    actual_selector: Literal["heuristic", "openai", "thaillm", "none"]
    model: str | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    latency_ms: int = Field(ge=0)


class AskTimings(ApiModel):
    retrieval_ms: int = Field(ge=0)
    llm_ms: int = Field(ge=0)
    validation_ms: int = Field(ge=0)
    graph_ms: int = Field(ge=0)
    total_ms: int = Field(ge=0)


class ConversationTurn(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class AskRequest(ApiModel):
    query: str = Field(min_length=1, max_length=1000)
    lemma: str | None = Field(default=None, min_length=1, max_length=100)
    context_lemma: str | None = Field(default=None, min_length=1, max_length=100)
    context_sense_uri: str | None = Field(default=None, min_length=1, max_length=500)
    hops: int = Field(default=1, ge=1, le=2)
    max_candidates: int = Field(default=8, ge=1, le=20)
    use_llm: bool | None = None
    history: list[ConversationTurn] = Field(default_factory=list, max_length=12)


class AskResponse(ApiModel):
    query: str
    detected_lemma: str | None
    candidates: list[SenseCandidate]
    selection: SelectionDecision
    answer: str
    detail: AnswerDetail
    citations: list[Citation]
    graph: GraphResult
    explanation_graph: GraphResult
    diagnostics: AskDiagnostics
    timings: AskTimings
    evidence_validated: bool
    validation_warnings: list[str] = Field(default_factory=list)


class SenseDetailResponse(ApiModel):
    candidate: SenseCandidate
    details: SenseLanguageDetails
    graph: GraphResult


class HealthResponse(ApiModel):
    status: Literal["ok"]
    graphdb: Literal["reachable"]
    repository: str
    selector_mode: Literal["heuristic", "openai", "thaillm"]
    llm_configured: bool
    llm_provider: Literal["openai", "thaillm"] | None = None
    llm_model: str | None = None
