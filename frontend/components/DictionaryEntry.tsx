"use client";

import { AlertCircle, BookOpen, ExternalLink, Link2, Network, Search } from "lucide-react";
import { useState } from "react";
import { displayEdition, displayLanguage, displayPos } from "@/lib/format";
import type {
  DictionaryEntryResponse,
  EntrySense,
  EntrySourceGroup,
  LanguageDetailValue,
  SenseCandidate,
} from "@/lib/types";

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
  coordinateTerm: "คำระดับเดียวกัน",
  derivedTerm: "คำที่สืบเนื่อง",
};

// Wording for reviewed links only; unreviewed proposals always read "อาจตรงกับ".
const matchNames: Record<string, string> = {
  exactMatch: "ตรงกับ",
  closeMatch: "ใกล้เคียงกับ",
  possiblySameSense: "อาจตรงกับ",
};

// Part-of-speech abbreviations used by the Royal Institute dictionaries.
const thaiPosAbbreviations: Record<string, string> = {
  "น.": "คำนาม",
  "ก.": "คำกริยา",
  "ว.": "คำวิเศษณ์",
  "วิ.": "คำวิเศษณ์",
  "สัน.": "คำสันธาน",
  "บ.": "คำบุพบท",
  "อ.": "คำอุทาน",
  "ส.": "คำสรรพนาม",
  "สรรพ.": "คำสรรพนาม",
  "นิ.": "คำนิบาต",
};

function senseGrammar(candidate: SenseCandidate) {
  if (candidate.pos && candidate.pos !== "unknown") return displayPos(candidate.pos);
  if (candidate.original_pos) return thaiPosAbbreviations[candidate.original_pos] || candidate.original_pos;
  return displayPos(null);
}

// Flags that only say a column was empty in the source; the page already shows
// what is present, so they are not repeated as warnings.
function visibleQualityFlags(flags: string[]) {
  return flags.filter((flag) => !flag.startsWith("missing-") && flag !== "unmapped-pos");
}

const RELATION_PREVIEW = 12;
const LINK_PREVIEW = 2;

// Wiktionary IPA already carries its slashes ("/kraʔ.duːk̚/").
function pronunciationText(value: string) {
  return `/${value.replace(/^\/+|\/+$/g, "")}/`;
}

// Some sources store a gloss in the sense-number field; only short ordinals are shown.
function senseOrdinal(value: string | null) {
  return value && /^[0-9๐-๙.()]{1,6}$/.test(value.trim()) ? value.trim() : null;
}

function MoreButton({ hidden, onClick }: { hidden: number; onClick: () => void }) {
  return (
    <button type="button" className="dict-more" onClick={onClick}>
      แสดงอีก {hidden} รายการ
    </button>
  );
}

const THAI = /[฀-๿]/;

function externalLink(value?: string | null) {
  return value?.startsWith("http://") || value?.startsWith("https://") ? value : null;
}

function sectionId(group: EntrySourceGroup) {
  return `source-${group.source_graph.replace(/[^a-z0-9]+/gi, "-")}`;
}

type ExampleRow = { text: string; translation: string | null; language: string | null };

function senseExamples(sense: EntrySense): ExampleRow[] {
  const rows: ExampleRow[] = sense.details.examples.map((item) => ({
    text: item.text,
    translation: item.translation,
    language: item.translation_language,
  }));
  const seen = new Set(rows.map((row) => row.text));
  for (const item of sense.candidate.evidence) {
    if (item.kind.endsWith("example") && !seen.has(item.text)) {
      rows.push({ text: item.text, translation: null, language: null });
      seen.add(item.text);
    }
  }
  return rows;
}

function ValueList({ items }: { items: LanguageDetailValue[] }) {
  return (
    <span className="dict-values">
      {items.map((item, index) => (
        <span key={`${item.value}-${index}`}>
          {item.value}
          {item.language && <small>{displayLanguage(item.language)}</small>}
          {!!item.tags.length && <small>{item.tags.join(", ")}</small>}
        </span>
      ))}
    </span>
  );
}

function DictionarySense({
  sense,
  number,
  onLookup,
  onShowGraph,
  graphBusy,
}: {
  sense: EntrySense;
  number: number;
  onLookup: (word: string) => void;
  onShowGraph: (candidate: SenseCandidate) => void;
  graphBusy: boolean;
}) {
  const { candidate, details, alignments } = sense;
  const [showAllRelations, setShowAllRelations] = useState(false);
  const [showAllLinks, setShowAllLinks] = useState(false);
  const ordinal = senseOrdinal(details.sense_number);
  const definitions = candidate.evidence.filter((item) => item.kind === "definition");
  const synsetDefinitions = candidate.evidence.filter((item) => item.kind === "synset-definition");
  const examples = senseExamples(sense);
  const relations = Object.entries(
    details.relations.reduce<Record<string, typeof details.relations>>((groups, item) => {
      (groups[item.relation] ||= []).push(item);
      return groups;
    }, {}),
  );
  const facts: { label: string; items: LanguageDetailValue[] }[] = [
    { label: "คำแปล", items: details.translations },
    { label: "การออกเสียง", items: details.pronunciations },
    { label: "ถอดอักษร", items: details.romanizations },
    { label: "รูปคำอื่น", items: details.forms },
    { label: "รากศัพท์", items: details.etymologies },
  ].filter((fact) => fact.items.length);

  return (
    <li className="dict-sense">
      <span className="dict-sense-number" aria-hidden="true">{number}</span>
      <div className="dict-sense-body">
        <div className="dict-sense-tags">
          <span className="pos-chip">{senseGrammar(candidate)}</span>
          {ordinal && <span className="dict-tag" title="ลำดับความหมายในต้นฉบับ">ความหมาย {ordinal}</span>}
          {candidate.register && <span className="dict-tag">{candidate.register === "ปาก" ? "ภาษาปาก" : candidate.register}</span>}
          {visibleQualityFlags(details.quality_flags).map((flag) => <span className="dict-tag warning" key={flag}>{flag}</span>)}
        </div>

        {definitions.length ? (
          definitions.map((item) => <p className="dict-definition" key={item.evidence_id}>{item.text}</p>)
        ) : !synsetDefinitions.length ? (
          <p className="dict-definition empty">แหล่งนี้ไม่มีนิยาม</p>
        ) : null}
        {synsetDefinitions.map((item) => (
          <p className="dict-definition synset" key={item.evidence_id}>
            <span className="language-chip">{displayLanguage(item.evidence_language || item.language)}</span>
            {item.text}
            <small>นิยามจาก WordNet ที่เชื่อมกับความหมายนี้</small>
          </p>
        ))}

        {!!examples.length && (
          <ul className="dict-examples" aria-label="ตัวอย่าง">
            {examples.map((example) => (
              <li key={example.text}>
                <span>{example.text}</span>
                {example.translation && <em>{example.translation}</em>}
              </li>
            ))}
          </ul>
        )}

        {!!facts.length && (
          <dl className="dict-facts">
            {facts.map((fact) => (
              <div key={fact.label}>
                <dt>{fact.label}</dt>
                <dd><ValueList items={fact.items} /></dd>
              </div>
            ))}
          </dl>
        )}

        {!!relations.length && (
          <dl className="dict-facts dict-relations">
            {relations.map(([relation, items]) => (
              <div key={relation}>
                <dt>{relationNames[relation] || relation}</dt>
                <dd className="dict-values">
                  {(showAllRelations ? items : items.slice(0, RELATION_PREVIEW)).map((item) =>
                    THAI.test(item.term) ? (
                      <button type="button" key={item.target_uri} onClick={() => onLookup(item.term)} title={`ค้นคำว่า ${item.term}`}>
                        {item.term}
                      </button>
                    ) : (
                      <span key={item.target_uri}>{item.term}</span>
                    ),
                  )}
                  {!showAllRelations && items.length > RELATION_PREVIEW && (
                    <MoreButton hidden={items.length - RELATION_PREVIEW} onClick={() => setShowAllRelations(true)} />
                  )}
                </dd>
              </div>
            ))}
          </dl>
        )}

        {!!alignments.length && (
          <ul className="dict-links" aria-label="ความหมายที่เชื่อมกับแหล่งอื่น">
            {(showAllLinks ? alignments : alignments.slice(0, LINK_PREVIEW)).map((link) => (
              <li key={link.other_sense_uri} className={link.review_status}>
                <Link2 aria-hidden="true" />
                <span>
                  {link.review_status === "approved" ? matchNames[link.relation] : "อาจตรงกับ"}ความหมายใน{" "}
                  <strong>{link.other_source_name}</strong>
                  {link.other_definition && <> “{link.other_definition}”</>}
                </span>
                <small>
                  {link.review_status === "approved" ? "ผู้เชี่ยวชาญยืนยันแล้ว" : "ข้อเสนออัตโนมัติ · รอตรวจ"}
                  {" · "}คะแนน {link.confidence.toFixed(2)}
                </small>
              </li>
            ))}
            {!showAllLinks && alignments.length > LINK_PREVIEW && (
              <li className="dict-links-more">
                <MoreButton hidden={alignments.length - LINK_PREVIEW} onClick={() => setShowAllLinks(true)} />
              </li>
            )}
          </ul>
        )}

        {!!details.source_notes.length && (
          <p className="dict-note-line">หมายเหตุจากแหล่ง: {details.source_notes.join(" · ")}</p>
        )}

        <div className="dict-sense-footer">
          {!!details.relations.length && (
            <button type="button" onClick={() => onShowGraph(candidate)} disabled={graphBusy}>
              <Network aria-hidden="true" /> {graphBusy ? "กำลังโหลดกราฟ…" : "ดูกราฟความสัมพันธ์"}
            </button>
          )}
          {(candidate.source_record_id || details.sense_number) && (
            <code title={candidate.sense_uri}>
              {candidate.source_record_id || `sense ${details.sense_number}`}
            </code>
          )}
        </div>
      </div>
    </li>
  );
}

export function DictionaryEntryView({
  entry,
  loading,
  onLookup,
  onShowGraph,
  busyUri,
}: {
  entry: DictionaryEntryResponse | null;
  loading: boolean;
  onLookup: (word: string) => void;
  onShowGraph: (candidate: SenseCandidate) => void;
  busyUri: string | null;
}) {
  if (loading && !entry) {
    return <div className="dict-state"><span className="button-loader" /> กำลังค้นคำในฐานข้อมูล…</div>;
  }
  if (!entry) {
    return (
      <div className="dict-state">
        <BookOpen aria-hidden="true" />
        <strong>สำรวจคำแบบพจนานุกรม</strong>
        <p>พิมพ์คำหนึ่งคำ แล้วดูทุกความหมาย ตัวอย่าง คำแปล คำสัมพันธ์ และที่มาจากทุกแหล่งข้อมูล</p>
      </div>
    );
  }
  if (!entry.found) {
    return (
      <div className="dict-state">
        <AlertCircle aria-hidden="true" />
        <strong>ไม่พบคำว่า “{entry.query}” ในชุดข้อมูลที่นำเข้า</strong>
        {entry.suggestions.length ? (
          <>
            <p>คำที่ใกล้เคียงในคลังคำ</p>
            <div className="dict-suggestions">
              {entry.suggestions.map((item) => (
                <button type="button" key={item.lemma} onClick={() => onLookup(item.lemma)}>
                  <Search aria-hidden="true" /> {item.lemma} <small>{item.sense_count} ความหมาย</small>
                </button>
              ))}
            </div>
          </>
        ) : (
          <p>ตรวจการสะกด หรือนำเข้าชุดข้อมูลที่มีคำนี้ก่อน</p>
        )}
      </div>
    );
  }

  return (
    <article className={`dict-entry${loading ? " is-refreshing" : ""}`} aria-labelledby="dict-headword">
      <header className="dict-headword">
        <div className="dict-headword-line">
          <h2 id="dict-headword">{entry.lemma}</h2>
          {[...entry.pronunciations, ...entry.romanizations].slice(0, 4).map((item, index) => (
            <span className="dict-pronunciation" key={`${item.value}-${index}`}>{pronunciationText(item.value)}</span>
          ))}
        </div>
        <div className="dict-headword-meta">
          {entry.parts_of_speech.map((pos) => <span className="pos-chip" key={pos}>{displayPos(pos)}</span>)}
          <span>{entry.sense_count} ความหมาย · {entry.source_count} แหล่งข้อมูล</span>
        </div>
        {entry.resolved_from === "sentence" && (
          <p className="dict-note-line">แสดงคำว่า “{entry.lemma}” ที่พบในข้อความ “{entry.query}”</p>
        )}
        {entry.truncated && (
          <p className="dict-note-line">คำนี้มีความหมายมาก แสดง {entry.sense_count} ความหมายแรก</p>
        )}
        {entry.groups.length > 1 && (
          <nav className="dict-source-index" aria-label="ไปยังแหล่งข้อมูล">
            {entry.groups.map((group) => (
              <a href={`#${sectionId(group)}`} key={group.source_graph}>
                {group.name} <small>{group.senses.length}</small>
              </a>
            ))}
          </nav>
        )}
      </header>

      {entry.groups.map((group) => (
        <section className="dict-source" id={sectionId(group)} key={group.source_graph} aria-label={group.name}>
          <header className="dict-source-head">
            <div>
              <h3>{group.name}</h3>
              <p>
                {[group.edition && displayEdition(group.edition), group.license && !externalLink(group.license) ? group.license : null]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </div>
            <div className="dict-source-links">
              {externalLink(group.source_url) && (
                <a href={group.source_url!} target="_blank" rel="noreferrer">แหล่งต้นทาง <ExternalLink aria-hidden="true" /></a>
              )}
              {externalLink(group.license) && (
                <a href={group.license!} target="_blank" rel="noreferrer">License <ExternalLink aria-hidden="true" /></a>
              )}
            </div>
          </header>
          <ol className="dict-senses">
            {group.senses.map((sense, index) => (
              <DictionarySense
                key={sense.candidate.sense_uri}
                sense={sense}
                number={index + 1}
                onLookup={onLookup}
                onShowGraph={onShowGraph}
                graphBusy={busyUri === sense.candidate.sense_uri}
              />
            ))}
          </ol>
        </section>
      ))}
    </article>
  );
}
