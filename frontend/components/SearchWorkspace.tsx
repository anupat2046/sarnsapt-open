"use client";

import {
  AlertCircle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Database,
  Layers3,
  Languages,
  PanelRightClose,
  PanelRightOpen,
  RotateCcw,
  Search,
  Send,
  ShieldAlert,
  Sparkles,
  X,
} from "lucide-react";
import {
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { askQuestion, getEntry, getSense, getSenseDetails } from "@/lib/api";
import {
  bestDefinition,
  displayEdition,
  displayLanguage,
  displayPos,
  displaySource,
  senseLabel,
  uriTail,
} from "@/lib/format";
import type {
  AskResponse,
  ConversationTurn,
  DictionaryEntryResponse,
  GraphResult,
  SenseCandidate,
  SenseLanguageDetails,
} from "@/lib/types";
import { DictionaryEntryView } from "./DictionaryEntry";
import { GraphExplorer, hasRenderableRelations } from "./GraphExplorer";
import { BrandMark } from "./BrandMark";

type ChatMessage = ConversationTurn & {
  id: string;
  selector?: AskResponse["selection"]["selector"];
  answerMode?: AskResponse["diagnostics"]["answer_mode"];
  intent?: AskResponse["detail"]["intent"];
  validated?: boolean;
  fallback?: boolean;
  fallbackReason?: string | null;
  latencyMs?: number;
  cueWords?: string[];
};

function selectorLabel(
  selector?: AskResponse["selection"]["selector"],
  intent?: AskResponse["detail"]["intent"],
  answerMode?: AskResponse["diagnostics"]["answer_mode"],
) {
  if (intent === "conversation") return "สนทนา";
  if (intent === "define_all") {
    return selector === "thaillm"
      ? "ThaiLLM วิเคราะห์คำถาม · ความหมายจาก GraphDB"
      : "ความหมายจาก GraphDB";
  }
  if (selector === "thaillm") return answerMode === "model" ? "ThaiLLM + GraphDB" : "ThaiLLM เลือกความหมาย · คำตอบจากกราฟ";
  if (intent === "compare") return "Compare + GraphDB";
  if (intent === "related") return "Relations + GraphDB";
  if (selector === "openai") return "LLM + GraphDB";
  if (selector === "heuristic") return "Heuristic + GraphDB";
  return "GraphDB";
}

function HighlightedText({ text, highlights }: { text: string; highlights: string[] }) {
  const escaped = highlights
    .filter(Boolean)
    .sort((left, right) => right.length - left.length)
    .map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!escaped.length) return <>{text}</>;
  const matcher = new RegExp(`(${escaped.join("|")})`, "g");
  return <>{text.split(matcher).map((part, index) =>
    highlights.includes(part)
      ? <mark key={`${part}-${index}`} className="context-cue">{part}</mark>
      : part
  )}</>;
}

function ChatAnswerContent({ content, cueWords = [] }: { content: string; cueWords?: string[] }) {
  const blocks = content.trim().split(/\n{2,}/).filter(Boolean);
  return (
    <div className="chat-answer-content">
      {blocks.map((block, blockIndex) => {
        const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
        if (lines.length && lines.every((line) => line.startsWith("• "))) {
          return (
            <ul key={`list-${blockIndex}`}>
              {lines.map((line) => (
                <li key={line}><HighlightedText text={line.slice(2)} highlights={cueWords} /></li>
              ))}
            </ul>
          );
        }
        if (
          lines.length
          && /^\d+\.\s/.test(lines[0])
          && lines.every((line) => /^\d+\.\s/.test(line) || line.startsWith("- "))
        ) {
          const items: { text: string; details: string[] }[] = [];
          for (const line of lines) {
            if (line.startsWith("- ")) items[items.length - 1].details.push(line.slice(2));
            else items.push({ text: line.replace(/^\d+\.\s*/, ""), details: [] });
          }
          return (
            <ol key={`list-${blockIndex}`}>
              {items.map((item, itemIndex) => (
                <li key={`${itemIndex}-${item.text}`}>
                  <HighlightedText text={item.text} highlights={cueWords} />
                  {item.details.map((detail) => (
                    <span className="chat-answer-detail" key={detail}>{detail}</span>
                  ))}
                </li>
              ))}
            </ol>
          );
        }
        return (
          <p key={`paragraph-${blockIndex}`}>
            <HighlightedText text={lines.join("\n")} highlights={cueWords} />
          </p>
        );
      })}
    </div>
  );
}

function fallbackReasonLabel(reason?: string | null) {
  if (!reason) return "ThaiLLM ไม่พร้อม จึงใช้ Heuristic";
  if (reason.includes("missing_choices")) return "ThaiLLM ไม่ส่งรายการคำตอบ จึงใช้ Heuristic";
  if (reason.includes("missing_message_content")) return "ThaiLLM ไม่ส่งเนื้อหาคำตอบ จึงใช้ Heuristic";
  if (reason.includes("missing_json_object")) return "ThaiLLM ไม่ส่ง JSON จึงใช้ Heuristic";
  if (reason.includes("invalid_json")) return "JSON จาก ThaiLLM ไม่สมบูรณ์ จึงใช้ Heuristic";
  if (reason.includes("json_not_object")) return "ThaiLLM ส่ง JSON ผิดโครงสร้าง จึงใช้ Heuristic";
  if (reason.includes("missing_required_fields")) return "ThaiLLM ส่งฟิลด์ Structured Output ไม่ครบ จึงใช้ Heuristic";
  if (reason.includes("schema_validation_failed")) return "Structured Output จาก ThaiLLM มีชนิดข้อมูลไม่ถูกต้อง จึงใช้ Heuristic";
  if (reason.includes("selected_sense_not_in_candidates")) return "ThaiLLM เลือก Sense นอก Candidate จึงใช้ Heuristic";
  if (reason.includes("evidence_not_owned_by_selected_sense")) return "ThaiLLM อ้าง Evidence ไม่ตรง Sense จึงใช้ Heuristic";
  if (reason.includes("ValueError")) return "ThaiLLM ส่ง Structured Output ไม่สมบูรณ์ จึงใช้ Heuristic";
  if (reason.includes("Timeout")) return "ThaiLLM ตอบช้าเกินกำหนด จึงใช้ Heuristic";
  if (reason.includes("llm_unavailable")) return "ThaiLLM ขัดข้องชั่วคราว จึงตอบจากข้อมูลในกราฟโดยตรง";
  if (reason.includes("HTTPStatusError")) {
    const status = reason.match(/HTTPStatusError:(\d{3})/)?.[1];
    if (status?.startsWith("5")) return `ThaiLLM ขัดข้องชั่วคราว (HTTP ${status}) จึงตอบจากข้อมูลในกราฟโดยตรง`;
    return `ThaiLLM API ปฏิเสธคำขอ${status ? ` (HTTP ${status})` : ""} จึงตอบจากข้อมูลในกราฟโดยตรง`;
  }
  if (reason.includes("define_all_with_context")) return "ThaiLLM ไม่เลือกความหมายทั้งที่มีบริบท จึงใช้ Heuristic เลือกจากบริบท";
  if (reason.includes("not_configured")) return "ยังไม่ได้ตั้งค่า ThaiLLM จึงใช้ Heuristic";
  return "ThaiLLM ไม่ผ่านการตรวจ จึงใช้ Heuristic";
}

function externalLink(value?: string | null) {
  return value?.startsWith("http://") || value?.startsWith("https://") ? value : null;
}

function contextualSourceName(
  source?: string | null,
  sourceGraph?: string | null,
  edition?: string | null,
  dataset?: string | null,
) {
  // Several imported datasets can share an edition title such as "v1", so the
  // dataset title is the name that tells them apart.
  if (dataset) return dataset;
  if (edition) return displayEdition(edition);
  if (source && source !== "organizer") return displaySource(source);
  const graph = sourceGraph || "";
  return graph ? decodeURIComponent(uriTail(graph)) : "ไม่ทราบแหล่งข้อมูล";
}

function candidateSourceName(candidate: SenseCandidate) {
  return contextualSourceName(
    candidate.sense_source || candidate.source,
    candidate.source_graph,
    candidate.edition,
    candidate.dataset,
  );
}

function candidateMeta(candidate: SenseCandidate) {
  const grammar = candidate.register === "ปาก"
    ? "ภาษาปาก"
    : displayPos(candidate.pos);
  return `${candidateSourceName(candidate)} · ${grammar}`;
}

function decodedTail(value: string) {
  const tail = value.split(/[\/#]/).filter(Boolean).at(-1) || value;
  try {
    return decodeURIComponent(tail);
  } catch {
    return tail;
  }
}

function wordNetSynsetId(value: string) {
  const tail = decodedTail(value);
  return tail.match(/(\d{8}-[nvars])$/i)?.[1] || tail;
}

function CandidateRail({
  candidates,
  selectedUri,
  onSelect,
  busyUri,
  contextMatchedUri,
}: {
  candidates: SenseCandidate[];
  selectedUri: string | null;
  onSelect: (candidate: SenseCandidate) => void;
  busyUri: string | null;
  contextMatchedUri: string | null;
}) {
  const displayCandidates = candidates;
  return (
    <aside className="sense-rail" aria-label="ความหมายที่เป็นไปได้">
      <h2>{displayCandidates.length} ความหมายของ “{displayCandidates[0]?.lemma || "คำนี้"}”</h2>
      <div className="sense-list" role="listbox" aria-label="เลือกความหมาย">
        {displayCandidates.map((candidate, index) => {
          const selected = candidate.sense_uri === selectedUri;
          return (
            <button
              type="button"
              role="option"
              aria-selected={selected}
              className={selected ? "sense-option selected" : "sense-option"}
              key={candidate.sense_uri}
              onClick={() => onSelect(candidate)}
              disabled={busyUri === candidate.sense_uri}
            >
              <span>
                <strong>{senseLabel(candidate, index)}</strong>
                {selected && contextMatchedUri === candidate.sense_uri && <em className="context-match">ตรงกับบริบทที่ถาม</em>}
                <small>{candidateMeta(candidate)}</small>
              </span>
              <ArrowRight aria-hidden="true" />
            </button>
          );
        })}
      </div>
    </aside>
  );
}

function EvidenceColumn({
  candidate,
  validated,
}: {
  candidate: SenseCandidate;
  validated: boolean;
}) {
  const definitions = candidate.evidence.filter((item) =>
    item.kind.includes("definition"),
  );
  const examples = candidate.evidence.filter((item) => item.kind.includes("example"));
  const evidenceGroups = [
    { label: "นิยาม", items: definitions },
    { label: "ตัวอย่าง", items: examples },
  ];
  const primaryDefinition = bestDefinition(candidate);
  const definition = primaryDefinition?.text || "ยังไม่มีนิยามในแหล่งนี้";
  const senseSource = candidate.sense_source || candidate.source;
  const isThaiWordNet = [candidate.source, senseSource].some((source) =>
    source === "omw-th" || source === "thai-wordnet"
  );
  const wordNetSenseId = candidate.source_record_id || decodedTail(candidate.sense_uri);
  const wordNetSynset = candidate.concept_uri
    ? wordNetSynsetId(candidate.concept_uri)
    : null;
  const linkedDefinition = definitions.find((item) => item.kind === "synset-definition");

  return (
    <article className="answer-panel" aria-labelledby="answer-lemma">
      <div className="answer-summary">
        <h2 id="answer-lemma">{candidate.lemma}</h2>
        <p className="answer-definition">{definition}</p>
        <div className="answer-meta">
          <span className="pos-chip">{displayPos(candidate.pos)}</span>
        </div>
        {isThaiWordNet && (
          <section className="wordnet-structure" aria-label="โครงสร้าง Thai WordNet">
            <div className="wordnet-structure-heading">
              <div>
                <strong>โครงสร้าง WordNet</strong>
                <small>คำไทยและ Sense จาก Thai WordNet เชื่อมหลักฐานผ่าน Synset</small>
              </div>
              <span>omw-th</span>
            </div>
            <dl>
              <div><dt>คำภาษาไทย</dt><dd>{candidate.lemma}</dd></div>
              <div><dt>ชนิดคำ</dt><dd>{displayPos(candidate.pos)}</dd></div>
              <div><dt>Sense ID</dt><dd><code>{wordNetSenseId}</code></dd></div>
              <div><dt>Synset ID</dt><dd><code>{wordNetSynset || "ไม่มี Concept URI"}</code></dd></div>
              <div className="wordnet-evidence-row">
                <dt>นิยามประกอบ</dt>
                <dd>
                  {linkedDefinition
                    ? `${displaySource(linkedDefinition.evidence_source || "omw-en")} · ${displayLanguage(linkedDefinition.evidence_language || linkedDefinition.language)}`
                    : "ยังไม่มีนิยามจาก English OMW"}
                </dd>
              </div>
            </dl>
            <details className="wordnet-technical-details">
              <summary>ดู URI เชิงเทคนิค</summary>
              <div><span>Sense URI</span><code>{candidate.sense_uri}</code></div>
              {candidate.concept_uri && <div><span>Concept URI</span><code>{candidate.concept_uri}</code></div>}
            </details>
          </section>
        )}
      </div>

      <div className="evidence-section">
        <div className="evidence-title-row">
          <h3>หลักฐานอ้างอิง</h3>
          <span className={validated ? "validation-state verified" : "validation-state pending"}>
            {validated ? <CheckCircle2 /> : <AlertCircle />}
            {validated ? "ตรวจสอบ Evidence แล้ว" : "รอตรวจสอบ"}
          </span>
        </div>
        <div className="evidence-timeline">
          {evidenceGroups.map((group) => (
            <section className="evidence-group" key={group.label}>
              <div className="timeline-dot" aria-hidden="true" />
              <h4>{group.label}</h4>
              {group.items.length ? (
                group.items.slice(0, 3).map((item) => (
                  <div className="evidence-fact" key={item.evidence_id}>
                    <p>{item.text}</p>
                    <div className="provenance-row">
                      <BookOpen aria-hidden="true" />
                      <span>
                        {item.kind.startsWith("synset-") ? "นิยามประกอบจาก" : "ที่มา"}: {" "}
                        <strong>{contextualSourceName(
                          item.evidence_source || senseSource,
                          item.source_graph,
                          item.edition,
                          item.source_graph === candidate.source_graph ? candidate.dataset : null,
                        )}</strong>
                      </span>
                      <span className="language-chip">
                        {displayLanguage(item.evidence_language || item.language)}
                      </span>
                      <code title={item.evidence_uri}>{uriTail(item.evidence_uri)}</code>
                    </div>
                    <div className="provenance-details">
                      {item.edition && <span>ฉบับ: {displayEdition(item.edition)}</span>}
                      {externalLink(item.source_url) && (
                        <a href={item.source_url!} target="_blank" rel="noreferrer">แหล่งต้นทาง</a>
                      )}
                      {externalLink(item.license) && (
                        <a href={item.license!} target="_blank" rel="noreferrer">License</a>
                      )}
                      {externalLink(item.attribution) && (
                        <a href={item.attribution!} target="_blank" rel="noreferrer">Attribution</a>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="evidence-fact empty-fact">
                  ไม่มี{group.label.toLowerCase()}ในแหล่งนี้
                </div>
              )}
            </section>
          ))}
        </div>
      </div>
    </article>
  );
}

const relationNames: Record<string, string> = {
  synonym: "คำพ้อง",
  antonym: "คำตรงข้าม",
  hypernym: "คำกว้างกว่า",
  hyponym: "คำเฉพาะกว่า",
  holonym: "องค์รวม",
  meronym: "ส่วนประกอบ",
  entails: "การกระทำที่ตามมา",
  causes: "เป็นเหตุให้",
  domainTopic: "หมวดความรู้",
  attribute: "คุณสมบัติ",
  similar: "ความหมายใกล้เคียง",
  relatedTerm: "คำที่เกี่ยวข้อง",
};

function LanguageDetailsPanel({ candidate }: { candidate: SenseCandidate }) {
  const [details, setDetails] = useState<SenseLanguageDetails | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (details || loading) return;
    setLoading(true);
    setError(null);
    try {
      const response = await getSenseDetails(candidate.sense_uri);
      setDetails(response.details);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลดข้อมูลภาษาไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  };

  const values = details && [
    { label: "การออกเสียง", items: details.pronunciations },
    { label: "รูปคำอื่น", items: details.forms },
    { label: "Romanization", items: details.romanizations },
    { label: "คำแปลภาษาอังกฤษ", items: details.translations },
    { label: "รากศัพท์", items: details.etymologies },
  ];

  return (
    <details
      className="language-details"
      onToggle={(event) => {
        if (event.currentTarget.open) void load();
      }}
    >
      <summary>
        <span className="disclosure-icon"><Languages aria-hidden="true" /></span>
        <span>
          <strong>ข้อมูลภาษาเพิ่มเติม</strong>
          <small>การออกเสียง รูปคำ รากศัพท์ คำแปล คำสัมพันธ์ และลิขสิทธิ์</small>
        </span>
        <ChevronDown aria-hidden="true" />
      </summary>
      <div className="language-details-body">
        {loading && <p className="detail-loading"><span className="button-loader" /> กำลังอ่านข้อมูลจาก GraphDB…</p>}
        {error && <p className="detail-error">{error}</p>}
        {details && (
          <>
            <div className="language-detail-meta">
              {details.sense_number && <span>Sense {details.sense_number}</span>}
              {details.quality_flags.map((flag) => <span key={flag}>Quality: {flag}</span>)}
            </div>
            <div className="language-detail-grid">
              {values?.filter((group) => group.items.length).map((group) => (
                <section key={group.label}>
                  <h4>{group.label}</h4>
                  <ul>
                    {group.items.map((item, index) => (
                      <li key={`${item.uri}-${item.value}-${index}`}>
                        <span>{item.value}</span>
                        <small>{displayLanguage(item.language)}{item.tags.length ? ` · ${item.tags.join(", ")}` : ""}</small>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
              {!!details.examples.length && (
                <section>
                  <h4>ตัวอย่างพร้อมคำแปล</h4>
                  <ul>
                    {details.examples.map((example, index) => (
                      <li key={`${example.uri}-${index}`}>
                        <span>{example.text}</span>
                        {example.translation && <em>{example.translation}</em>}
                        <small>{displayLanguage(example.language)}{example.translation_language ? ` → ${displayLanguage(example.translation_language)}` : ""}</small>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {!!details.relations.length && (
                <section className="language-relations">
                  <h4>คำสัมพันธ์</h4>
                  <div>
                    {details.relations.slice(0, 18).map((relation) => (
                      <span key={`${relation.relation}-${relation.target_uri}`}>
                        <small>{relationNames[relation.relation] || relation.relation}</small>
                        {relation.term}
                      </span>
                    ))}
                  </div>
                </section>
              )}
              {!!details.source_notes.length && (
                <section><h4>หมายเหตุจากต้นฉบับ</h4><ul>{details.source_notes.map((note) => <li key={note}>{note}</li>)}</ul></section>
              )}
            </div>
            <div className="language-license">
              <strong>ข้อมูลต้นฉบับและสิทธิ์การใช้</strong>
              <code title={candidate.source_graph}>{displaySource(candidate.source)} · {uriTail(candidate.source_graph)}</code>
              {details.license && (externalLink(details.license)
                ? <a href={details.license} target="_blank" rel="noreferrer">License</a>
                : <span>{details.license}</span>)}
              {details.attribution && (externalLink(details.attribution)
                ? <a href={details.attribution} target="_blank" rel="noreferrer">Attribution</a>
                : <span>{details.attribution}</span>)}
            </div>
            {!values?.some((group) => group.items.length) && !details.examples.length && !details.relations.length && (
              <p className="detail-empty">Sense นี้ยังไม่มีข้อมูลภาษาเพิ่มเติมในแหล่งต้นฉบับ</p>
            )}
          </>
        )}
      </div>
    </details>
  );
}

function supportStatus(support: AskResponse["detail"]["source_supports"][number]) {
  if (support.review_status === "selected") return "ใช้ตอบในครั้งนี้";
  if (support.review_status === "approved") return "ผ่านการตรวจความสัมพันธ์";
  if (support.review_status === "pending") return "รอตรวจผู้เชี่ยวชาญ";
  return "ข้อมูลจากต้นฉบับ";
}

function supportSourceName(support: AskResponse["detail"]["source_supports"][number]) {
  return contextualSourceName(
    support.evidence_source || support.source,
    support.source_graph,
    support.edition,
    support.dataset,
  );
}

function supportTypeLabel(support: AskResponse["detail"]["source_supports"][number]) {
  return support.edition ? "ฉบับข้อมูล" : "แหล่งข้อมูลภาษา";
}

function GroundedDetailPanel({ result }: { result: AskResponse }) {
  const { detail, diagnostics, timings } = result;
  const isComparison = detail.intent === "compare";
  const sourceCount = new Set(detail.source_supports.map((item) => item.source_graph)).size;
  const supportSection = !!detail.source_supports.length && (
    <div className="support-section">
      <div className="section-mini-heading">
        <Layers3 aria-hidden="true" />
        <div>
          <strong>{isComparison ? "ข้อมูลจากหลาย Dataset" : "หลักฐานจากหลาย Dataset"}</strong>
          <span>
            {isComparison
              ? "แสดงข้อมูลแต่ละแหล่งแยกกัน เพื่อให้เห็นความต่างของแต่ละฉบับ"
              : "ข้อความทุกชิ้นยังแยกตามแหล่งและฉบับ จึงตรวจย้อนกลับได้"}
          </span>
        </div>
      </div>
      <div className="support-grid">
        {detail.source_supports.map((support) => (
          <article
            className={`support-card ${support.review_status}`}
            key={`${support.source_graph}-${support.edition}-${support.sense_uri}`}
          >
            <div className="support-card-head">
              <strong>{supportSourceName(support)}</strong>
              <span>{supportTypeLabel(support)}</span>
            </div>
            <p>{support.definition || "ยังไม่มีนิยามใน Source Sense นี้"}</p>
            <div className="support-summary-meta">
              {support.definition && <span className="language-chip">{displayLanguage(support.evidence_language)}</span>}
              {support.edition && <span>{displayEdition(support.edition)}</span>}
            </div>
            <details className="support-card-details">
              <summary>ดูรายละเอียด</summary>
              <dl>
                <div><dt>สถานะ</dt><dd>{supportStatus(support)}</dd></div>
                <div><dt>ชนิดคำ</dt><dd>{displayPos(support.pos)}</dd></div>
                <div><dt>การเชื่อมความหมาย</dt><dd>{support.match_relation}</dd></div>
                {externalLink(support.source_url) && (
                  <div><dt>ต้นทาง</dt><dd><a href={support.source_url!} target="_blank" rel="noreferrer">เปิดแหล่งข้อมูล</a></dd></div>
                )}
              </dl>
            </details>
          </article>
        ))}
      </div>
    </div>
  );

  return (
      <section className="grounded-panel-content" aria-label="ตรวจสอบคำตอบและแหล่งที่มา">
        <header className="active-panel-heading">
          <span className="disclosure-icon"><BookOpen aria-hidden="true" /></span>
          <span>
            <strong>ตรวจสอบคำตอบและแหล่งที่มา</strong>
            <small>{result.citations.length} หลักฐาน · {sourceCount} แหล่งข้อมูล</small>
          </span>
        </header>
        <div className="verification-body">
          {diagnostics.fallback_used && (
            <div className="fallback-notice" role="status">
              <ShieldAlert aria-hidden="true" />
              <div>
                <strong>ใช้แผนสำรองอย่างโปร่งใส</strong>
                <span>{fallbackReasonLabel(diagnostics.fallback_reason)}</span>
              </div>
            </div>
          )}
          {supportSection}
          <details className="processing-details">
            <summary>ดูขั้นตอนและเวลาประมวลผล</summary>
            <p className="grounded-explanation">{detail.explanation}</p>
            <div className="grounded-stats">
              <span><Database />Intent: {detail.intent}</span>
              <span><BookOpen />{result.citations.length} หลักฐาน</span>
              <span>ค้นข้อมูล {timings.retrieval_ms} ms</span>
              <span>LLM {timings.llm_ms} ms</span>
              <span>ตรวจหลักฐาน {timings.validation_ms} ms</span>
              <span>สร้างกราฟ {timings.graph_ms} ms</span>
              <span>รวม {(timings.total_ms / 1000).toFixed(2)} วินาที</span>
            </div>
            <ol className="reasoning-path">
              {detail.reasoning_path.map((step) => (
                <li key={step.step}>
                  <span>{step.step}</span>
                  <div><strong>{step.label}</strong><p>{step.detail}</p></div>
                </li>
              ))}
            </ol>
          </details>
        </div>
      </section>
  );
}

type InspectionPanel = "graph" | "evidence" | "senses";
type WorkspaceMode = "chat" | "explore";

function WorkspaceModeSwitch({
  mode,
  onChange,
}: {
  mode: WorkspaceMode;
  onChange: (mode: WorkspaceMode) => void;
}) {
  return (
    <div className="workspace-mode-switch" role="group" aria-label="รูปแบบการใช้งาน">
      <button type="button" aria-pressed={mode === "chat"} onClick={() => onChange("chat")}>ถามสานศัพท์</button>
      <button type="button" aria-pressed={mode === "explore"} onClick={() => onChange("explore")}>สำรวจคำ</button>
    </div>
  );
}

const LANDING_TYPEWRITER_TEXT = "ผ่านความหมายและบริบท";

export function SearchWorkspace() {
  const [view, setView] = useState<"landing" | "workspace">("landing");
  const [mode, setMode] = useState<WorkspaceMode>("chat");
  const [activePanel, setActivePanel] = useState<InspectionPanel>("graph");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorWidth, setInspectorWidth] = useState(420);
  const [resizing, setResizing] = useState(false);
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [inspectionResult, setInspectionResult] = useState<AskResponse | null>(null);
  const [entry, setEntry] = useState<DictionaryEntryResponse | null>(null);
  const [selected, setSelected] = useState<SenseCandidate | null>(null);
  const [graph, setGraph] = useState<GraphResult>({
    hops: 2,
    nodes: [],
    edges: [],
    truncated: false,
  });
  const [loading, setLoading] = useState(false);
  const [busyUri, setBusyUri] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastQuestion, setLastQuestion] = useState("");
  const [lastMode, setLastMode] = useState<WorkspaceMode>("chat");
  const [landingTypedText, setLandingTypedText] = useState("");
  const transcriptRef = useRef<HTMLDivElement>(null);
  const splitRef = useRef<HTMLDivElement>(null);
  const resizingRef = useRef(false);

  useEffect(() => {
    const graphemes = Array.from(
      new Intl.Segmenter("th", { granularity: "grapheme" }).segment(
        LANDING_TYPEWRITER_TEXT,
      ),
      ({ segment }) => segment,
    );
    let index = 0;
    let deleting = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = () => {
      if (!deleting) {
        if (index < graphemes.length) {
          index += 1;
          setLandingTypedText(graphemes.slice(0, index).join(""));
          timer = setTimeout(tick, 82);
          return;
        }
        deleting = true;
        timer = setTimeout(tick, 1800);
        return;
      }

      if (index > 0) {
        index -= 1;
        setLandingTypedText(graphemes.slice(0, index).join(""));
        timer = setTimeout(tick, 42);
        return;
      }

      deleting = false;
      timer = setTimeout(tick, 480);
    };

    timer = setTimeout(tick, 420);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (view !== "workspace") return;
    // A chat follows the newest message; a dictionary entry is read from the top.
    transcriptRef.current?.scrollTo(
      mode === "explore"
        ? { top: 0 }
        : { top: transcriptRef.current.scrollHeight, behavior: "smooth" },
    );
  }, [loading, messages, view, mode, entry]);

  const analyze = useCallback(
    async (
      question: string,
      conversation: ConversationTurn[] = [],
      contextLemma?: string | null,
      contextSenseUri?: string | null,
      requestedMode: WorkspaceMode = "chat",
      recordUser = true,
    ) => {
      const normalized = question.trim();
      if (!normalized) return;

      if (requestedMode === "explore") {
        setLastQuestion(normalized);
        setLastMode(requestedMode);
        setLoading(true);
        setError(null);
        try {
          const response = await getEntry(normalized);
          setEntry(response);
          setView("workspace");
          setSelected(null);
          setGraph({ hops: 2, nodes: [], edges: [], truncated: false });
          setInspectorOpen(false);
        } catch (requestError) {
          setError(requestError instanceof Error ? requestError.message : "ไม่สามารถเชื่อมต่อระบบได้");
        } finally {
          setLoading(false);
        }
        return;
      }

      const userMessage: ChatMessage = {
        id: `user-${Date.now()}-${Math.random()}`,
        role: "user",
        content: normalized,
      };
      setLastQuestion(normalized);
      setLastMode(requestedMode);
      if (recordUser) setMessages((current) => [...current, userMessage]);
      setLoading(true);
      setError(null);

      try {
        const response = await askQuestion(
          normalized,
          conversation,
          contextLemma,
          contextSenseUri,
        );
        setResult(response);
        setView("workspace");
        const initial =
          response.candidates.find(
            (candidate) => candidate.sense_uri === response.selection.selected_sense_uri,
          ) ||
          response.candidates[0] ||
          null;
        if (response.detail.intent !== "conversation") {
          setInspectionResult(response);
          setSelected(initial);
          setGraph(response.graph);
          setActivePanel(
            hasRenderableRelations(response.graph, initial?.sense_uri)
              ? "graph"
              : response.candidates.length ? "senses" : "evidence",
          );
        }
        setMessages((current) => [
          ...current,
          {
            id: `assistant-${Date.now()}-${Math.random()}`,
            role: "assistant",
            content: response.answer,
            selector: response.selection.selector,
            answerMode: response.diagnostics.answer_mode,
            intent: response.detail.intent,
            validated: response.evidence_validated,
            fallback: response.diagnostics.fallback_used,
            fallbackReason: response.diagnostics.fallback_reason,
            latencyMs: response.diagnostics.latency_ms,
            cueWords: response.selection.cue_words,
          },
        ]);
      } catch (requestError) {
        setError(
          requestError instanceof Error
            ? requestError.message
            : "ไม่สามารถเชื่อมต่อระบบได้",
        );
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const showEntryGraph = async (candidate: SenseCandidate) => {
    setSelected(candidate);
    setBusyUri(candidate.sense_uri);
    setError(null);
    try {
      const detail = await getSense(candidate.sense_uri);
      setGraph(detail.graph);
      setInspectorOpen(true);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลดกราฟไม่สำเร็จ");
    } finally {
      setBusyUri(null);
    }
  };

  const selectCandidate = async (candidate: SenseCandidate) => {
    if (candidate.sense_uri === selected?.sense_uri) return;
    setSelected(candidate);
    setBusyUri(candidate.sense_uri);
    try {
      const detail = await getSense(candidate.sense_uri);
      setSelected({ ...detail.candidate, retrieval_score: candidate.retrieval_score });
      setGraph(detail.graph);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "โหลด Sense ไม่สำเร็จ");
    } finally {
      setBusyUri(null);
    }
  };

  const conversationHistory = useMemo<ConversationTurn[]>(
    // Earlier answers only give the model conversational context; long meaning
    // listings are shortened so follow-up requests stay small.
    () => messages.map(({ role, content }) => ({ role, content: content.slice(0, 1200) })).slice(-12),
    [messages],
  );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const question = query;
    setQuery("");
    void analyze(
      question,
      conversationHistory,
      selected?.lemma || inspectionResult?.detected_lemma || result?.detected_lemma,
      result?.selection.selected_sense_uri || inspectionResult?.selection.selected_sense_uri,
      mode,
    );
  };

  const clearConversation = () => {
    setView("landing");
    setActivePanel("graph");
    setMessages([]);
    setResult(null);
    setInspectionResult(null);
    setEntry(null);
    setInspectorOpen(false);
    setSelected(null);
    setGraph({ hops: 2, nodes: [], edges: [], truncated: false });
    setQuery("");
    setError(null);
    setLastQuestion("");
  };

  const changeMode = (nextMode: WorkspaceMode) => {
    setMode(nextMode);
    if (view === "workspace") setInspectorOpen(false);
  };

  const retry = () => {
    const previousHistory = messages.at(-1)?.role === "user"
      ? conversationHistory.slice(0, -1)
      : conversationHistory;
    void analyze(
      lastQuestion,
      previousHistory,
      selected?.lemma || result?.detected_lemma,
      result?.selection.selected_sense_uri,
      lastMode,
      false,
    );
  };

  const clampInspectorWidth = (width: number) => {
    const available = splitRef.current?.getBoundingClientRect().width ?? window.innerWidth;
    return Math.round(Math.max(300, Math.min(width, Math.min(720, available - 440))));
  };

  const moveDivider = (event: PointerEvent<HTMLDivElement>) => {
    if (!resizingRef.current || !splitRef.current) return;
    const bounds = splitRef.current.getBoundingClientRect();
    setInspectorWidth(clampInspectorWidth(bounds.right - event.clientX));
  };

  const stopResizing = (event: PointerEvent<HTMLDivElement>) => {
    if (!resizingRef.current) return;
    resizingRef.current = false;
    setResizing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const resizeWithKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      setInspectorWidth((current) => clampInspectorWidth(current + (event.key === "ArrowLeft" ? 24 : -24)));
    } else if (event.key === "Home") {
      event.preventDefault();
      setInspectorWidth(clampInspectorWidth(300));
    } else if (event.key === "End") {
      event.preventDefault();
      setInspectorWidth(clampInspectorWidth(720));
    }
  };

  if (view === "landing") {
    return (
      <main className="landing-page" aria-labelledby="landing-heading">
        <svg className="landing-network" viewBox="0 0 1440 780" preserveAspectRatio="none" aria-hidden="true">
          <g className="network-lines">
            <path d="M0 210 C180 130 290 360 450 265 S710 165 850 290 S1120 390 1440 210" />
            <path d="M0 520 C180 610 330 430 520 550 S840 680 1040 500 S1250 390 1440 560" />
            <path d="M120 70 C230 210 185 520 360 700" />
            <path d="M1290 70 C1170 230 1260 540 1060 710" />
          </g>
          <g className="network-nodes">
            <circle cx="90" cy="245" r="7" /><circle cx="215" cy="178" r="4" />
            <circle cx="360" cy="318" r="10" /><circle cx="510" cy="245" r="5" />
            <circle cx="820" cy="274" r="6" /><circle cx="1040" cy="372" r="9" />
            <circle cx="1220" cy="250" r="5" /><circle cx="1360" cy="170" r="8" />
            <circle cx="180" cy="570" r="8" /><circle cx="460" cy="520" r="5" />
            <circle cx="720" cy="620" r="9" /><circle cx="1110" cy="510" r="6" />
            <circle cx="1310" cy="590" r="10" />
          </g>
        </svg>
        <section className="landing-hero">
          <h1 id="landing-heading">
            <span className="landing-ai-line"><b className="landing-brand-word">สานศัพท์</b> เชื่อมความรู้คำไทย</span>
            <br />
            <span className="sr-only">{LANDING_TYPEWRITER_TEXT}</span>
            <span className="landing-typewriter" aria-hidden="true">
              {landingTypedText}<i className="typewriter-caret" />
            </span>
          </h1>
          <p>ค้นความหมาย เปรียบเทียบฉบับ และสำรวจความเชื่อมโยงของคำ</p>
          <WorkspaceModeSwitch mode={mode} onChange={changeMode} />
          <form className="landing-search" onSubmit={submit}>
            <label>
              <Search aria-hidden="true" />
              <span className="sr-only">ถามเรื่องคำไทย</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={mode === "chat" ? "ถามเรื่องคำไทย…" : "พิมพ์คำที่ต้องการสำรวจ…"}
                maxLength={1000}
                autoFocus
              />
            </label>
            <button type="submit" disabled={loading || !query.trim()} aria-label="ส่งคำถาม">
              {loading ? <span className="button-loader" /> : <Send aria-hidden="true" />}
            </button>
          </form>
          {loading && <p className="landing-loading"><Sparkles /> {mode === "chat" ? "กำลังอ่านคำถามและค้นข้อมูล…" : "กำลังค้นคำในฐานข้อมูล…"}</p>}
          {error && (
            <div className="landing-error" role="alert">
              <AlertCircle /> <span>{error}</span>
              <button type="button" onClick={retry}>ลองอีกครั้ง</button>
            </div>
          )}
        </section>
      </main>
    );
  }

  return (
    <main className="result-app-shell">
      <header className="workspace-toolbar">
        <button type="button" className="brand workspace-brand" onClick={clearConversation} aria-label="สานศัพท์ กลับหน้าแรก">
          <BrandMark /><span>สานศัพท์</span>
        </button>
        <WorkspaceModeSwitch mode={mode} onChange={changeMode} />
        <div className="workspace-toolbar-actions">
          <button
            type="button"
            className="inspector-toggle"
            onClick={() => setInspectorOpen((open) => !open)}
            aria-controls="word-inspector"
            aria-expanded={inspectorOpen}
          >
            {inspectorOpen ? <PanelRightClose aria-hidden="true" /> : <PanelRightOpen aria-hidden="true" />}
            <span>{inspectorOpen ? "ซ่อนข้อมูลคำ" : "ดูข้อมูลคำ"}</span>
          </button>
          <button type="button" className="workspace-reset" onClick={clearConversation} disabled={loading} aria-label="เริ่มบทสนทนาใหม่">
            <RotateCcw aria-hidden="true" /> <span>เริ่มใหม่</span>
          </button>
        </div>
      </header>

      <div
        ref={splitRef}
        className={`result-split${inspectorOpen ? " inspector-open" : ""}${resizing ? " is-resizing" : ""}`}
        style={{ "--inspector-width": `${inspectorWidth}px` } as CSSProperties}
      >
        <section className="conversation-pane" aria-label="สานศัพท์แชตบอต">
          <div ref={transcriptRef} className="workspace-transcript" aria-live="polite" aria-label="บทสนทนา">
            <div className="transcript-inner">
            {mode === "explore" && (
              <DictionaryEntryView
                entry={entry}
                loading={loading}
                busyUri={busyUri}
                onLookup={(word) => void analyze(word, [], null, null, "explore")}
                onShowGraph={(candidate) => void showEntryGraph(candidate)}
              />
            )}
            {mode === "chat" && messages.map((message) => (
              <div className={`chat-row ${message.role}`} key={message.id}>
                <div className="chat-bubble">
                  <span className="chat-role">{message.role === "user" ? "คุณ" : "สานศัพท์"}</span>
                  {message.role === "assistant"
                    ? <ChatAnswerContent content={message.content} cueWords={message.cueWords} />
                    : <p>{message.content}</p>}
                  {message.role === "assistant" && (
                    <div className="chat-answer-meta">
                      <span>{selectorLabel(message.selector, message.intent, message.answerMode)}</span>
                      {message.validated && <span className="chat-verified"><CheckCircle2 /> {message.intent === "define_all" ? "อ้างอิงตรวจแล้ว" : "Sense/อ้างอิงตรวจแล้ว"}</span>}
                      {message.latencyMs !== undefined && <span>{(message.latencyMs / 1000).toFixed(1)}s</span>}
                    </div>
                  )}
                  {message.role === "assistant" && message.fallback && (
                    <div className="chat-fallback"><ShieldAlert />{fallbackReasonLabel(message.fallbackReason)}</div>
                  )}
                </div>
              </div>
            ))}
            {mode === "chat" && loading && (
              <div className="chat-row assistant">
                <div className="chat-bubble chat-thinking"><span className="button-loader" /><span>{mode === "chat" ? "กำลังอ่านคำถามและตรวจข้อมูล…" : "กำลังค้นคำในฐานข้อมูล…"}</span></div>
              </div>
            )}
            {error && (
              <div className="workspace-error" role="alert">
                <AlertCircle /><span><strong>เชื่อมต่อข้อมูลไม่สำเร็จ</strong>{error}</span>
                <button type="button" onClick={retry}>ลองอีกครั้ง</button>
              </div>
            )}
            </div>
          </div>

          <form className="workspace-composer" onSubmit={submit}>
            <label>
              <span className="sr-only">ข้อความที่ต้องการถามสานศัพท์</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={mode === "chat" ? "ถามต่อเกี่ยวกับคำไทย…" : "พิมพ์คำที่ต้องการสำรวจ…"}
                maxLength={1000}
              />
            </label>
            <button type="submit" disabled={loading || !query.trim()} aria-label="ส่งคำถาม">
              {loading ? <span className="button-loader" /> : <Send aria-hidden="true" />}
            </button>
          </form>
        </section>

        {inspectorOpen && (
          <>
            <button type="button" className="inspector-scrim" onClick={() => setInspectorOpen(false)} aria-label="ปิดแผงข้อมูลคำ" />
            <div
              className="inspector-resizer"
              role="separator"
              tabIndex={0}
              aria-label="ปรับความกว้างแผงข้อมูลคำ"
              aria-orientation="vertical"
              aria-controls="word-inspector"
              aria-valuemin={300}
              aria-valuemax={720}
              aria-valuenow={inspectorWidth}
              onPointerDown={(event) => {
                resizingRef.current = true;
                setResizing(true);
                event.currentTarget.setPointerCapture(event.pointerId);
                event.preventDefault();
              }}
              onPointerMove={moveDivider}
              onPointerUp={stopResizing}
              onPointerCancel={stopResizing}
              onKeyDown={resizeWithKeyboard}
              onDoubleClick={() => setInspectorWidth(420)}
            ><span aria-hidden="true" /></div>
            <aside id="word-inspector" className="knowledge-pane" aria-label="ข้อมูลคำและหลักฐาน">
              <header className="inspector-header">
                <div>
                  <strong>{mode === "explore" ? entry?.lemma || selected?.lemma || "ข้อมูลคำ" : inspectionResult?.detected_lemma || selected?.lemma || "ข้อมูลคำ"}</strong>
                  <small>{mode === "explore" ? "กราฟความสัมพันธ์ของความหมายที่เลือก" : inspectionResult ? `${inspectionResult.candidates.length} รายการความหมาย · ${inspectionResult.citations.length} หลักฐาน` : "กราฟ ความหมาย และแหล่งข้อมูล"}</small>
                </div>
                <button type="button" onClick={() => setInspectorOpen(false)} aria-label="ปิดแผงข้อมูลคำ"><X aria-hidden="true" /></button>
              </header>
              {mode === "explore" ? (
                selected ? (
                  <section className="result-stage result-stage-graph" aria-live="polite">
                    <GraphExplorer graph={graph} selected={selected} cueWords={[]} />
                  </section>
                ) : (
                  <div className="inspector-empty"><BookOpen aria-hidden="true" /><strong>เลือกความหมายเพื่อดูกราฟ</strong><p>กด “ดูกราฟความสัมพันธ์” ใต้ความหมายที่สนใจ</p></div>
                )
              ) : inspectionResult ? (
                <>
                  <nav className="inspector-tabs" aria-label="เลือกข้อมูลประกอบคำตอบ">
                    <button type="button" className={activePanel === "graph" ? "active" : ""} aria-current={activePanel === "graph" ? "page" : undefined} onClick={() => setActivePanel("graph")}>กราฟ</button>
                    <button type="button" className={activePanel === "evidence" ? "active" : ""} aria-current={activePanel === "evidence" ? "page" : undefined} onClick={() => setActivePanel("evidence")}>หลักฐาน</button>
                    <button type="button" className={activePanel === "senses" ? "active" : ""} aria-current={activePanel === "senses" ? "page" : undefined} onClick={() => setActivePanel("senses")}>ความหมาย</button>
                  </nav>
                  <section className={`result-stage result-stage-${activePanel}`} aria-live="polite">
                    {activePanel === "graph" && (
                      <GraphExplorer
                        graph={inspectionResult.detail.intent === "compare" ? { hops: 2, nodes: [], edges: [], truncated: false } : graph}
                        selected={inspectionResult.detail.intent === "compare" ? null : selected}
                        cueWords={inspectionResult.selection.cue_words}
                      />
                    )}
                    {activePanel === "evidence" && <GroundedDetailPanel result={inspectionResult} />}
                    {activePanel === "senses" && selected && (
                      <div className="sense-panel-content">
                        <section className="sense-evidence-workspace" aria-label="ความหมายและหลักฐานรายความหมาย">
                          <CandidateRail
                            candidates={inspectionResult.candidates}
                            selectedUri={selected.sense_uri}
                            contextMatchedUri={inspectionResult.selection.selected_sense_uri}
                            onSelect={selectCandidate}
                            busyUri={busyUri}
                          />
                          <EvidenceColumn candidate={selected} validated={inspectionResult.evidence_validated} />
                        </section>
                        <LanguageDetailsPanel key={selected.sense_uri} candidate={selected} />
                      </div>
                    )}
                  </section>
                </>
              ) : (
                <div className="inspector-empty"><BookOpen aria-hidden="true" /><strong>ข้อมูลคำจะอยู่ตรงนี้</strong><p>ถามเรื่องคำไทยหรือเลือก “สำรวจคำ” แล้วเปิดดูความหมาย กราฟ และแหล่งข้อมูลได้</p></div>
              )}
            </aside>
          </>
        )}
      </div>

    </main>
  );
}
