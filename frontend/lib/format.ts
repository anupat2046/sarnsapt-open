import type { EvidenceItem, SenseCandidate } from "./types";

export const sourceLabel: Record<string, string> = {
  organizer: "ชุดข้อมูลนำเข้า",
  lexitron: "LEXiTRON",
  "thai-wordnet": "Thai WordNet · เชื่อม English OMW",
  "omw-en": "English OMW",
  wiktionary: "English Wiktionary · รายการคำภาษาไทย",
  "en-wiktionary-thai-entries": "English Wiktionary · รายการคำภาษาไทย",
  "th-wiktionary": "วิกิพจนานุกรมภาษาไทย",
  demo: "ข้อมูลสาธิต",
};

export const posLabel: Record<string, string> = {
  noun: "คำนาม",
  verb: "คำกริยา",
  adjective: "คำคุณศัพท์",
  adverb: "คำวิเศษณ์",
  pronoun: "คำสรรพนาม",
  preposition: "คำบุพบท",
  conjunction: "คำสันธาน",
  interjection: "คำอุทาน",
  classifier: "คำลักษณนาม",
  properNoun: "วิสามานยนาม",
  numeral: "คำบอกจำนวน",
  determiner: "คำกำหนด",
  particle: "คำอนุภาค",
  prefix: "คำอุปสรรค",
  suffix: "คำปัจจัย",
  infix: "คำอาคม",
  phrase: "วลี",
  proverb: "สุภาษิต",
  character: "อักขระ",
  punctuation: "เครื่องหมายวรรคตอน",
  symbol: "สัญลักษณ์",
};

export function displaySource(source: string): string {
  return sourceLabel[source] || source;
}

export function displayPos(pos: string | null): string {
  if (!pos) return "ไม่ระบุชนิดคำ";
  return posLabel[pos] || pos;
}

export function displayLanguage(language: string | null | undefined): string {
  return language ? language.split("-")[0].toUpperCase() : "—";
}

export function displayEdition(edition: string): string {
  return edition.replace(" (ไฟล์ที่ได้รับยังไม่สมบูรณ์)", "");
}

export function bestDefinition(candidate: SenseCandidate): EvidenceItem | undefined {
  return (
    candidate.evidence.find((item) => item.kind === "definition") ||
    candidate.evidence.find((item) => item.kind === "synset-definition")
  );
}

export function bestExample(candidate: SenseCandidate): EvidenceItem | undefined {
  return (
    candidate.evidence.find((item) => item.kind === "example") ||
    candidate.evidence.find((item) => item.kind === "synset-example")
  );
}

export function senseLabel(candidate: SenseCandidate, index: number): string {
  const definition = bestDefinition(candidate)?.text;
  if (!definition) return `ความหมายที่ ${index + 1}`;
  return definition.length > 34 ? `${definition.slice(0, 34)}…` : definition;
}

export function uriTail(uri: string): string {
  const decoded = decodeURIComponent(uri.split(/[\/#]/).filter(Boolean).at(-1) || uri);
  return decoded.replace(/[-_]/g, " ");
}
