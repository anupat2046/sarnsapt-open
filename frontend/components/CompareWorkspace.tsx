"use client";

import { AlertCircle, BookOpen, ChevronDown, Info, Search } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { getAlignments, getSenses } from "@/lib/api";
import {
  bestDefinition,
  bestExample,
  displayPos,
  displaySource,
  senseLabel,
  uriTail,
} from "@/lib/format";
import type { AlignmentCandidate, SenseCandidate } from "@/lib/types";

const SOURCE_ORDER = [
  "organizer", "lexitron", "thai-wordnet", "th-wiktionary",
  "en-wiktionary-thai-entries", "wiktionary",
];

interface Comparable {
  candidate: SenseCandidate | null;
  alignment: AlignmentCandidate | null;
}

function alignmentRank(item: AlignmentCandidate): number {
  if (item.review_status === "approved") return 10 + item.confidence;
  if (item.review_status === "pending" && item.recommended_relation === "exactMatch") return 1.01 + item.confidence;
  if (item.review_status === "pending" && item.recommended_relation === "closeMatch") return 1 + item.confidence;
  return 0;
}

function chooseComparable(
  anchor: SenseCandidate,
  candidates: SenseCandidate[],
  alignments: AlignmentCandidate[],
): Comparable {
  const candidateUris = new Set(candidates.map((item) => item.sense_uri));
  const matched = alignments
    .filter((item) => item.review_status !== "rejected")
    .filter((item) => item.review_status === "approved" || item.confidence >= 0.85)
    .filter((item) => item.recommended_relation !== "possiblySameSense" || item.review_status === "approved")
    .filter((item) => item.left_sense_uri === anchor.sense_uri || item.right_sense_uri === anchor.sense_uri)
    .map((item) => ({
      alignment: item,
      targetUri: item.left_sense_uri === anchor.sense_uri ? item.right_sense_uri : item.left_sense_uri,
    }))
    .filter((item) => candidateUris.has(item.targetUri))
    .sort((a, b) => alignmentRank(b.alignment) - alignmentRank(a.alignment))[0];
  if (!matched) return { candidate: null, alignment: null };
  return {
    candidate: candidates.find((item) => item.sense_uri === matched.targetUri) || null,
    alignment: matched.alignment,
  };
}

function SourceCell({ candidate, kind }: { candidate: SenseCandidate | null; kind: "definition" | "pos" | "example" | "source" }) {
  if (!candidate) return <span className="missing-value">ไม่มีข้อมูลในแหล่งนี้</span>;
  if (kind === "definition") return <>{bestDefinition(candidate)?.text || <span className="missing-value">ไม่มีนิยามในแหล่งนี้</span>}</>;
  if (kind === "pos") return <>{displayPos(candidate.pos)}</>;
  if (kind === "example") return <>{bestExample(candidate)?.text || <span className="missing-value">ไม่มีตัวอย่างในแหล่งนี้</span>}</>;
  return (
    <span className="source-record">
      <BookOpen aria-hidden="true" />
      <span>{displaySource(candidate.source)} · {candidate.source_record_id || uriTail(candidate.sense_uri)}</span>
      <ChevronDown aria-hidden="true" />
    </span>
  );
}

export function CompareWorkspace() {
  const [query, setQuery] = useState("ขัน");
  const [lemma, setLemma] = useState("ขัน");
  const [candidates, setCandidates] = useState<SenseCandidate[]>([]);
  const [alignments, setAlignments] = useState<AlignmentCandidate[]>([]);
  const [anchorUri, setAnchorUri] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (word: string) => {
    const normalized = word.trim();
    if (!normalized) return;
    setLoading(true);
    setError(null);
    try {
      const [response, alignmentResponse] = await Promise.all([
        getSenses(normalized),
        getAlignments(normalized),
      ]);
      const usable = response.candidates.filter((item) => SOURCE_ORDER.includes(item.source));
      setLemma(response.lemma);
      setCandidates(usable);
      setAlignments(alignmentResponse.candidates);
      const anchor = usable.find((item) => item.source === "lexitron") || usable[0] || null;
      setAnchorUri(anchor?.sense_uri || null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "ไม่สามารถโหลดข้อมูลเปรียบเทียบได้");
      setCandidates([]);
      setAlignments([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load("ขัน"), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const groups = useMemo(() => {
    const mapped = new Map<string, SenseCandidate[]>();
    for (const source of SOURCE_ORDER) mapped.set(source, []);
    for (const candidate of candidates) mapped.get(candidate.source)?.push(candidate);
    return mapped;
  }, [candidates]);

  const sources = useMemo(
    () => SOURCE_ORDER.filter((source) => (groups.get(source)?.length || 0) > 0),
    [groups],
  );
  const anchorCandidates = groups.get("lexitron")?.length
    ? groups.get("lexitron")!
    : candidates.slice(0, 8);
  const anchor = candidates.find((item) => item.sense_uri === anchorUri) || anchorCandidates[0] || null;
  const anchorIndex = anchor
    ? Math.max(0, anchorCandidates.findIndex((item) => item.sense_uri === anchor.sense_uri))
    : 0;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void load(query);
  };

  return (
    <main className="page-shell compare-page">
      <section className="compare-intro" aria-labelledby="compare-heading">
        <h1 id="compare-heading">เปรียบเทียบความหมายข้ามแหล่ง</h1>
        <form className="compare-form" onSubmit={submit}>
          <label className="question-input compare-input">
            <Search aria-hidden="true" />
            <span className="sr-only">คำที่ต้องการเปรียบเทียบ</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} maxLength={100} />
          </label>
          <button className="primary-button" type="submit" disabled={loading || !query.trim()}>
            {loading ? <span className="button-loader" /> : null}
            {loading ? "กำลังโหลด" : "เปรียบเทียบ"}
          </button>
        </form>
        <div className="compare-notice">
          <Info aria-hidden="true" />
          <span><strong>เปรียบเทียบจาก Alignment ของ Phase 3 เท่านั้น</strong> ระบบจะไม่เดาคู่จากชนิดคำหรือความคล้ายผิวเผิน หากหลักฐานไม่พอจะแสดงว่าไม่พบคู่ที่น่าเชื่อถือ</span>
        </div>
      </section>

      {error && (
        <div className="error-banner" role="alert">
          <AlertCircle aria-hidden="true" />
          <span><strong>โหลดข้อมูลไม่สำเร็จ</strong>{error}</span>
          <button type="button" onClick={() => void load(query)}>ลองอีกครั้ง</button>
        </div>
      )}

      {!loading && !error && !candidates.length && (
        <div className="empty-state"><BookOpen /><h2>ไม่พบคำว่า “{lemma}”</h2><p>ลองค้นด้วยรูปเขียนอื่น หรือกลับไปตรวจข้อมูลใน GraphDB</p></div>
      )}

      {candidates.length > 0 && anchor && (
        <section className="compare-workspace" aria-label={`เปรียบเทียบคำว่า ${lemma}`}>
          <aside className="compare-sense-rail">
            <h2>เลือกความหมาย</h2>
            <div role="listbox" aria-label="เลือกความหมายหลักเพื่อเปรียบเทียบ">
              {anchorCandidates.slice(0, 8).map((candidate, index) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={candidate.sense_uri === anchor.sense_uri}
                  className={candidate.sense_uri === anchor.sense_uri ? "compare-sense selected" : "compare-sense"}
                  key={candidate.sense_uri}
                  onClick={() => setAnchorUri(candidate.sense_uri)}
                >
                  <span>{index + 1}</span>
                  <strong>{senseLabel(candidate, index)}</strong>
                </button>
              ))}
            </div>
          </aside>

          <div className="comparison-scroll">
            <div className="comparison-matrix" style={{ "--source-count": sources.length } as React.CSSProperties}>
              <div className="matrix-corner">คำว่า “{lemma}”</div>
              {sources.map((source) => (
                <div className="source-column-heading" key={source}>
                  <strong>{displaySource(source)}</strong>
                </div>
              ))}
              {[anchor].filter((item): item is SenseCandidate => item !== null).map((groupAnchor) => {
                const selectedCandidates: Comparable[] = sources.map((source) =>
                  source === groupAnchor.source
                    ? { candidate: groupAnchor, alignment: null }
                    : chooseComparable(groupAnchor, groups.get(source) || [], alignments),
                );
                const strongest = selectedCandidates
                  .map((item) => item.alignment)
                  .filter((item): item is AlignmentCandidate => item !== null)
                  .sort((a, b) => alignmentRank(b) - alignmentRank(a))[0];
                const isFocused = groupAnchor.sense_uri === anchor.sense_uri;
                return (
                  <div className="matrix-group" key={groupAnchor.sense_uri}>
                    <div className={isFocused ? "relation-strip matrix-full focused" : "relation-strip matrix-full"}>
                      <span>{anchorIndex + 1}. {senseLabel(groupAnchor, anchorIndex)}</span>
                      <i />
                      <strong className={strongest?.review_status === "approved" ? "relation-approved" : undefined}>
                        {strongest?.review_status === "approved"
                          ? `ผู้เชี่ยวชาญยืนยัน ${strongest.decided_relation}`
                          : strongest
                            ? `ข้อเสนอ ${strongest.recommended_relation} · ${Math.round(strongest.confidence * 100)}%`
                            : "ยังไม่พบคู่ข้ามแหล่ง"}
                      </strong>
                    </div>
                    {([
                      ["ความหมาย", "definition"],
                      ["ชนิดคำ", "pos"],
                      ["ตัวอย่าง", "example"],
                      ["แหล่งอ้างอิง", "source"],
                    ] as const).map(([label, kind]) => (
                      <div className="matrix-row" key={`${groupAnchor.sense_uri}-${kind}`}>
                        <div className="row-label">{label}</div>
                        {selectedCandidates.map(({ candidate, alignment }, index) => (
                          <div className={kind === "definition" && index === 0 ? "source-cell highlighted" : "source-cell"} key={`${groupAnchor.sense_uri}-${sources[index]}-${kind}`}>
                            <span className="mobile-source-label">{displaySource(sources[index])}</span>
                            <SourceCell candidate={candidate} kind={kind} />
                            {kind === "definition" && alignment && (
                              <span className={alignment.review_status === "approved" ? "alignment-chip approved" : "alignment-chip"}>
                                {alignment.review_status === "approved" ? "ยืนยันแล้ว" : "รอตรวจ"} · {Math.round(alignment.confidence * 100)}%
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
