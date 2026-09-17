import type {
  AlignmentsResponse,
  AskResponse,
  ConversationTurn,
  ReviewDecision,
  ReviewQueueResponse,
  SenseDetailResponse,
  SensesResponse,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ||
  "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `API request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function askQuestion(
  query: string,
  history: ConversationTurn[] = [],
  contextLemma?: string | null,
  contextSenseUri?: string | null,
): Promise<AskResponse> {
  return request<AskResponse>("/api/ask", {
    method: "POST",
    body: JSON.stringify({
      query,
      hops: 2,
      max_candidates: 12,
      history: history.slice(-12),
      context_lemma: contextLemma || undefined,
      context_sense_uri: contextSenseUri || undefined,
    }),
  });
}

export function getSense(uri: string): Promise<SenseDetailResponse> {
  const params = new URLSearchParams({ uri, hops: "2" });
  return request<SenseDetailResponse>(`/api/sense?${params}`);
}

export function getSenseDetails(uri: string): Promise<SenseDetailResponse> {
  const params = new URLSearchParams({ uri, hops: "2" });
  return request<SenseDetailResponse>(`/api/sense/details?${params}`);
}

export function getSenses(lemma: string): Promise<SensesResponse> {
  const params = new URLSearchParams({ lemma, limit: "100" });
  return request<SensesResponse>(`/api/senses?${params}`);
}

export function getAlignments(lemma: string): Promise<AlignmentsResponse> {
  const params = new URLSearchParams({ lemma, limit: "300" });
  return request<AlignmentsResponse>(`/api/alignments?${params}`);
}

export function getReviewQueue(lemma?: string): Promise<ReviewQueueResponse> {
  const params = new URLSearchParams({ limit: "40" });
  if (lemma?.trim()) params.set("lemma", lemma.trim());
  return request<ReviewQueueResponse>(`/api/review/candidates?${params}`);
}

export function saveReviewDecision(decision: ReviewDecision): Promise<{ saved: boolean }> {
  return request<{ saved: boolean }>("/api/review/decisions", {
    method: "POST",
    body: JSON.stringify(decision),
  });
}
