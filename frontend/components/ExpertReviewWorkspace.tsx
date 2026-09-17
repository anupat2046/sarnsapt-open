"use client";

import { AlertCircle, ArrowLeft, ArrowRight, Check, Search, ShieldCheck, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { getReviewQueue, saveReviewDecision } from "@/lib/api";
import { bestDefinition, bestExample, displayPos, displaySource } from "@/lib/format";
import type { ReviewCandidate, ReviewDecision } from "@/lib/types";

type DecisionKind = "exactMatch" | "closeMatch" | "reject";

function EvidenceCard({ side, label }: { side: ReviewCandidate["left"]; label: string }) {
  return (
    <article className="review-evidence-card">
      <div className="review-source-line">
        <span>{label}</span>
        <strong>{displaySource(side.source)}</strong>
      </div>
      <h3>{side.lemma}</h3>
      <span className="pos-chip">{displayPos(side.pos)}</span>
      <dl>
        <div><dt>ความหมาย</dt><dd>{bestDefinition(side)?.text || "ไม่มีนิยามในแหล่งนี้"}</dd></div>
        <div><dt>ตัวอย่าง</dt><dd>{bestExample(side)?.text || "ไม่มีตัวอย่างในแหล่งนี้"}</dd></div>
        <div><dt>รหัสข้อมูล</dt><dd><code>{side.source_record_id || side.sense_uri}</code></dd></div>
      </dl>
    </article>
  );
}

export function ExpertReviewWorkspace() {
  const [query, setQuery] = useState("");
  const [queue, setQueue] = useState<ReviewCandidate[]>([]);
  const [index, setIndex] = useState(0);
  const [decision, setDecision] = useState<DecisionKind | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  const load = useCallback(async (lemma?: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await getReviewQueue(lemma);
      setQueue(response.candidates);
      setIndex(0);
      setDecision(null);
      setNote("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลดคิวตรวจไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const current = queue[index] || null;
  const reviewedCount = useMemo(
    () => queue.filter((item) => item.alignment.review_status !== "pending").length,
    [queue],
  );

  const search = (event: FormEvent) => {
    event.preventDefault();
    void load(query.trim() || undefined);
  };

  const move = (next: number) => {
    setIndex(Math.max(0, Math.min(queue.length - 1, next)));
    setDecision(null);
    setNote("");
    setSavedMessage(null);
  };

  const save = async () => {
    if (!current || !decision || !reviewer.trim() || !note.trim()) return;
    const payload: ReviewDecision = {
      candidate_id: current.alignment.candidate_id,
      review_status: decision === "reject" ? "rejected" : "approved",
      relation: decision === "reject" ? null : decision,
      reviewer: reviewer.trim(),
      note: note.trim(),
    };
    setSaving(true);
    setError(null);
    try {
      await saveReviewDecision(payload);
      setQueue((items) => items.map((item, itemIndex) => itemIndex === index
        ? { ...item, alignment: { ...item.alignment, review_status: payload.review_status, decided_relation: payload.relation, reviewer: payload.reviewer, review_note: payload.note } }
        : item));
      setSavedMessage("บันทึกผลตรวจใน Reviewed Graph แล้ว");
      const nextPending = queue.findIndex((item, itemIndex) => itemIndex > index && item.alignment.review_status === "pending");
      if (nextPending >= 0) window.setTimeout(() => move(nextPending), 700);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "บันทึกผลตรวจไม่สำเร็จ");
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="page-shell review-page">
      <section className="review-intro">
        <div>
          <span className="eyebrow"><ShieldCheck /> Expert Review</span>
          <h1>ตรวจคู่ความหมายก่อนรับรอง</h1>
          <p>เทียบหลักฐานสองแหล่งแล้วตัดสิน exactMatch, closeMatch หรือปฏิเสธ ทุกผลตรวจแยกจากข้อมูลต้นฉบับและมี Audit Trail</p>
        </div>
        <form className="review-search" onSubmit={search}>
          <label><Search /><span className="sr-only">กรองด้วยคำ</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="กรอง เช่น ขัน" /></label>
          <button type="submit">ค้นในคิว</button>
        </form>
      </section>

      <div className="review-statusbar">
        <span><strong>{queue.length}</strong> คู่ในคิวนี้</span>
        <span><strong>{reviewedCount}</strong> ตรวจแล้ว</span>
        <span><strong>{queue.length - reviewedCount}</strong> รอตรวจ</span>
        <i><span style={{ width: `${queue.length ? (reviewedCount / queue.length) * 100 : 0}%` }} /></i>
      </div>

      {error && <div className="error-banner" role="alert"><AlertCircle /><span><strong>เกิดข้อผิดพลาด</strong>{error}</span></div>}
      {loading && <div className="review-loading">กำลังโหลด Candidate จาก Alignment Graph…</div>}
      {!loading && !current && <div className="empty-state"><ShieldCheck /><h2>ไม่พบ Candidate ในคิวนี้</h2><p>ลองล้างคำค้น หรือเลือกคำอื่นที่อยู่ในชุด Prototype</p></div>}

      {current && (
        <section className="review-workspace" aria-label="ตรวจคู่ความหมาย">
          <div className="review-card-heading">
            <div>
              <span>Candidate {index + 1} / {queue.length}</span>
              <h2>คำว่า “{current.left.lemma}”</h2>
            </div>
            <div className="candidate-score">
              <strong>{Math.round(current.alignment.confidence * 100)}%</strong>
              <span>คะแนนคัดกรอง</span>
            </div>
          </div>

          <div className="review-comparison">
            <EvidenceCard side={current.left} label="แหล่งที่ 1" />
            <div className="review-bridge">
              <span>{current.alignment.recommended_relation}</span>
              <i />
              <small>คำแนะนำระบบ<br />ไม่ใช่ข้อเท็จจริง</small>
            </div>
            <EvidenceCard side={current.right} label="แหล่งที่ 2" />
          </div>

          <div className="review-decision-panel">
            <div>
              <h3>ผลการตรวจ</h3>
              <div className="decision-options" role="radiogroup" aria-label="ผลการตรวจ">
                <button type="button" role="radio" aria-checked={decision === "exactMatch"} className={decision === "exactMatch" ? "selected" : ""} onClick={() => setDecision("exactMatch")}><Check />ตรงกัน (exact)</button>
                <button type="button" role="radio" aria-checked={decision === "closeMatch"} className={decision === "closeMatch" ? "selected" : ""} onClick={() => setDecision("closeMatch")}><Check />ใกล้เคียง (close)</button>
                <button type="button" role="radio" aria-checked={decision === "reject"} className={decision === "reject" ? "selected reject" : ""} onClick={() => setDecision("reject")}><X />ไม่ใช่คู่เดียวกัน</button>
              </div>
            </div>
            <div className="review-fields">
              <label>ผู้ตรวจ<input value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="ชื่อหรือรหัสผู้เชี่ยวชาญ" /></label>
              <label>เหตุผลประกอบ<textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="ระบุหลักฐานทางภาษาและเหตุผลที่ตัดสิน" rows={3} /></label>
            </div>
            <div className="review-actions">
              <button type="button" className="secondary-button" onClick={() => move(index - 1)} disabled={index === 0}><ArrowLeft />ก่อนหน้า</button>
              <span className="saved-message" aria-live="polite">{savedMessage}</span>
              <button type="button" className="save-decision" onClick={() => void save()} disabled={!decision || !reviewer.trim() || !note.trim() || saving}>{saving ? "กำลังบันทึก" : "บันทึกผลตรวจ"}<ArrowRight /></button>
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
