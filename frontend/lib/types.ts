export type EvidenceKind =
  | "definition"
  | "example"
  | "synset-definition"
  | "synset-example";

export interface EvidenceItem {
  evidence_id: string;
  kind: EvidenceKind;
  text: string;
  language: string | null;
  evidence_uri: string;
  source_graph: string;
  derived_from: string | null;
  sense_source: string | null;
  evidence_source: string | null;
  evidence_language: string | null;
  edition: string | null;
  edition_uri: string | null;
  source_url: string | null;
  license: string | null;
  attribution: string | null;
}

export interface SenseCandidate {
  sense_uri: string;
  entry_uri: string;
  concept_uri: string | null;
  lemma: string;
  pos: string | null;
  original_pos: string | null;
  register: string | null;
  source: string;
  source_graph: string;
  source_record_id: string | null;
  sense_source: string | null;
  edition: string | null;
  edition_uri: string | null;
  source_url: string | null;
  license: string | null;
  attribution: string | null;
  evidence: EvidenceItem[];
  retrieval_score: number;
}

export interface GraphNode {
  uri: string;
  label: string;
  kind: "sense" | "concept" | "resource";
  source_graph?: string | null;
}

export interface GraphEdge {
  source: string;
  predicate: string;
  target: string;
  source_graph: string;
  hop: 1 | 2;
}

export interface GraphResult {
  hops: 1 | 2;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}

export interface Citation {
  evidence_id: string;
  kind: string;
  text: string;
  evidence_uri: string;
  source: string;
  source_graph: string;
  derived_from: string | null;
  sense_source: string | null;
  evidence_source: string | null;
  evidence_language: string | null;
  edition: string | null;
  source_url: string | null;
  license: string | null;
  attribution: string | null;
}

export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

export interface SourceSupport {
  source: string;
  sense_uri: string;
  pos: string | null;
  definition: string | null;
  example: string | null;
  match_relation: "selected" | "exactMatch" | "closeMatch" | "possiblySameSense" | "unlinked";
  confidence: number | null;
  review_status: "selected" | "approved" | "pending" | "unlinked";
  evidence_ids: string[];
  sense_source: string | null;
  evidence_source: string | null;
  evidence_language: string | null;
  source_graph: string | null;
  edition: string | null;
  source_url: string | null;
  license: string | null;
  attribution: string | null;
}

export interface RelatedTerm {
  term: string;
  relation: string;
  target_uri: string;
  source_graph: string;
}

export interface ReasoningStep {
  step: number;
  label: string;
  detail: string;
  evidence_ids: string[];
}

export interface AnswerDetail {
  intent: "define" | "define_all" | "related" | "compare";
  title: string;
  summary: string;
  explanation: string;
  source_supports: SourceSupport[];
  related_terms: RelatedTerm[];
  reasoning_path: ReasoningStep[];
}

export interface AskDiagnostics {
  requested_provider: "heuristic" | "openai" | "thaillm";
  actual_selector: "heuristic" | "openai" | "thaillm" | "none";
  model: string | null;
  fallback_used: boolean;
  fallback_reason: string | null;
  latency_ms: number;
}

export interface AskTimings {
  retrieval_ms: number;
  llm_ms: number;
  validation_ms: number;
  graph_ms: number;
  total_ms: number;
}

export interface AskResponse {
  query: string;
  detected_lemma: string | null;
  candidates: SenseCandidate[];
  selection: {
    selected_sense_uri: string | null;
    confidence: number;
    rationale: string;
    evidence_ids: string[];
    selector: "heuristic" | "openai" | "thaillm" | "none";
    intent: "define" | "define_all" | "related" | "compare" | null;
    cue_words: string[];
    grounded_answer: string | null;
  };
  answer: string;
  detail: AnswerDetail;
  citations: Citation[];
  graph: GraphResult;
  explanation_graph: GraphResult;
  diagnostics: AskDiagnostics;
  timings: AskTimings;
  evidence_validated: boolean;
  validation_warnings: string[];
}

export interface HealthResponse {
  status: "ok";
  graphdb: "reachable";
  repository: string;
  selector_mode: "heuristic" | "openai" | "thaillm";
  llm_configured: boolean;
  llm_provider: "openai" | "thaillm" | null;
  llm_model: string | null;
}

export interface SensesResponse {
  lemma: string;
  candidates: SenseCandidate[];
  evidence_validated: boolean;
}

export interface AlignmentCandidate {
  candidate_id: string;
  assertion_uri: string;
  left_sense_uri: string;
  right_sense_uri: string;
  confidence: number;
  semantic_similarity: number;
  pos_compatibility: number;
  method: string;
  recommended_relation: "exactMatch" | "closeMatch" | "possiblySameSense";
  review_status: "pending" | "approved" | "rejected";
  decided_relation: "exactMatch" | "closeMatch" | null;
  reviewer: string | null;
  review_note: string | null;
}

export interface AlignmentsResponse {
  lemma: string | null;
  candidates: AlignmentCandidate[];
}

export interface ReviewCandidate {
  alignment: AlignmentCandidate;
  left: SenseCandidate;
  right: SenseCandidate;
}

export interface ReviewQueueResponse {
  lemma: string | null;
  candidates: ReviewCandidate[];
}

export interface ReviewDecision {
  candidate_id: string;
  review_status: "approved" | "rejected";
  relation: "exactMatch" | "closeMatch" | null;
  reviewer: string;
  note: string;
}

export interface SenseDetailResponse {
  candidate: SenseCandidate;
  details: SenseLanguageDetails;
  graph: GraphResult;
}

export interface LanguageDetailValue {
  value: string;
  language: string | null;
  uri: string | null;
  source_graph: string;
  tags: string[];
}

export interface ExampleDetail {
  text: string;
  language: string | null;
  translation: string | null;
  translation_language: string | null;
  uri: string | null;
  source_graph: string;
}

export interface SenseRelationDetail {
  relation: string;
  term: string;
  target_uri: string;
  language: string | null;
  source_graph: string;
}

export interface SenseLanguageDetails {
  sense_number: string | null;
  translations: LanguageDetailValue[];
  pronunciations: LanguageDetailValue[];
  forms: LanguageDetailValue[];
  romanizations: LanguageDetailValue[];
  etymologies: LanguageDetailValue[];
  examples: ExampleDetail[];
  relations: SenseRelationDetail[];
  quality_flags: string[];
  source_notes: string[];
  license: string | null;
  attribution: string | null;
}
