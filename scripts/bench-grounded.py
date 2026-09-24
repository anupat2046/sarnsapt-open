"""Does the knowledge graph make the same LLM better? A/B/C benchmark.

Three arms answer the same questions about the same words:

    model-only    ThaiLLM alone, no dictionary data
    plain-dict    ThaiLLM plus a flat list of definitions from one source
    sarn-sap      the full system (question analysis, graph retrieval, answer)

Words are sampled from GraphDB in strata, because an average over common words
hides the interesting part: a small model already knows "พ่อ" and "ดาว".

    common      3+ senses in 3+ sources
    rare        only in the 2542 Royal Institute dictionary
    technical   only in the organizer technical glossaries
    dialect     only in the organizer dialect word lists
    fake        a real word with one character changed; it is in no source

Automatic scores are deliberately simple and stated as proxies:

    meaning_match   the answer shares content words with a reference definition
    invented        a fake word received a definition instead of "not found"
    cited_source    the answer names a source that really carries the word

`meaning_match` favours answers that quote the dictionary, which the grounded
arms do by design, so treat it as a screen and read the CSV (or pass --judge)
before drawing conclusions. Every answer is written to the CSV for review.

    python scripts/bench-grounded.py --per-stratum 8
    python scripts/bench-grounded.py --arms model-only,sarn-sap --judge
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from thailex_api.thai_text import BUILTIN_WORDS, segment  # noqa: E402

GRAPHDB = "http://127.0.0.1:7200/repositories/thailex"
BACKEND = "http://127.0.0.1:8000"
GRAPH = "https://w3id.org/thailex/graph"
# Words used as examples inside the system prompts; never test on them.
PROMPT_EXAMPLES = {"ผอม", "แมว", "ตำ", "น้ำ", "หนาว", "เรือ"}
ABSTAINED = re.compile(r"ไม่พบ|ไม่รู้จัก|ไม่มีในข้อมูล|ไม่ทราบ|ไม่ใช่คำ|ไม่ปรากฏ|ไม่มีคำ|ไม่มีความหมาย")
CITATION = re.compile(r"\(ที่มา:[^)]*\)|ที่มา:.*$", re.M)
THAI_LETTER = "กขคฆงจฉชซญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ"


def sparql(query: str, timeout: int = 600) -> list[dict[str, str]]:
    request = urllib.request.Request(
        GRAPHDB, data=urllib.parse.urlencode({"query": query}).encode(),
        headers={"Accept": "application/sparql-results+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return [
            {key: value["value"] for key, value in row.items()}
            for row in json.load(response)["results"]["bindings"]
        ]


def thaillm_settings() -> dict[str, str]:
    values = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("THAILLM_") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    for key in ("THAILLM_API_KEY", "THAILLM_MODEL", "THAILLM_BASE_URL"):
        values[key] = os.environ.get(key) or values.get(key, "")
    values.setdefault("THAILLM_BASE_URL", "https://thaillm.or.th/api/v1")
    if not values["THAILLM_API_KEY"]:
        raise SystemExit("THAILLM_API_KEY not found in .env or the environment")
    return values


def thaillm(settings: dict[str, str], system: str, user: str, max_tokens: int = 1200) -> tuple[str, float, str | None]:
    """Returns (answer, seconds, error). Retries once; outages are recorded, not raised.

    The budget is generous because this is a thinking model: with too little room
    it spends the whole allowance inside <think> and returns no answer, which
    would punish the arms that reason instead of quoting a definition.
    """
    body = json.dumps({
        "model": settings["THAILLM_MODEL"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": 0.0, "stream": False,
    }, ensure_ascii=False).encode()
    url = settings["THAILLM_BASE_URL"].rstrip("/") + "/chat/completions"
    started = time.time()
    error = None
    for attempt in range(2):
        request = urllib.request.Request(
            url, data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings['THAILLM_API_KEY']}",
                # Cloudflare in front of thaillm.or.th rejects urllib's default agent with 1010.
                "User-Agent": "httpx/0.27.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.load(response)
            content = payload["choices"][0]["message"].get("content") or ""
            answer = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
            if not answer or "<think>" in answer:
                # The reply ended inside its own reasoning: no answer was given,
                # so it is recorded as a failed call rather than a wrong one.
                return "", time.time() - started, "unfinished"
            return answer, time.time() - started, None
        except urllib.error.HTTPError as exc:
            error = f"http_{exc.code}"
        except Exception as exc:  # noqa: BLE001 - outage shapes vary
            error = type(exc).__name__
        if attempt == 0:
            time.sleep(2)
    return "", time.time() - started, error


def content_words(text: str) -> set[str]:
    return {
        token.text for token in segment(text)
        if len(token.text) >= 2 and token.text not in BUILTIN_WORDS
    }


def meaning_match(answer: str, references: list[str]) -> bool:
    """Proxy: the answer repeats enough content words of one reference definition."""
    found = content_words(answer)
    for reference in references:
        words = content_words(reference)
        if not words:
            continue
        shared = len(found & words)
        if shared >= 2 or (shared and shared >= len(words) * 0.5):
            return True
    return False


# --- sampling ---------------------------------------------------------------

_SOURCE_FILTER = f"""FILTER(?graph IN (
  <{GRAPH}/lexitron>, <{GRAPH}/th-wiktionary>, <{GRAPH}/en-wiktionary-thai-entries>, <{GRAPH}/thai-wordnet>)
  || STRSTARTS(STR(?graph), "{GRAPH}/organizer/"))"""
# Plain Thai words only: no abbreviations ("ก.พ."), affix stubs ("-ประจิม"), or spaces.
_LEMMA_FILTER = r"""FILTER(LANG(?lemma) = "th" && STRLEN(STR(?lemma)) >= 3)
  FILTER(REGEX(STR(?lemma), "^[\\u0E01-\\u0E2E][\\u0E01-\\u0E4E]*$"))"""


def _shuffled(seed: int, expression: str = "?lemma") -> str:
    """Deterministic pseudo-random ordering, so a seed picks a spread of the alphabet."""
    return f'ORDER BY MD5(CONCAT(STR({expression}), "{seed}"))'


def sample_common(limit: int, seed: int) -> list[str]:
    rows = sparql(f"""
PREFIX ontolex: <http://www.w3.org/ns/lemon/ontolex#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?lemma WHERE {{
  {{ SELECT ?lemma (COUNT(DISTINCT ?graph) AS ?sources) (COUNT(DISTINCT ?sense) AS ?senses) WHERE {{
       GRAPH ?graph {{ ?entry a ontolex:LexicalEntry ; rdfs:label ?lemma ; ontolex:sense ?sense }}
       {_SOURCE_FILTER}
       {_LEMMA_FILTER}
     }} GROUP BY ?lemma HAVING (COUNT(DISTINCT ?graph) >= 3 && COUNT(DISTINCT ?sense) >= 3) }}
}} {_shuffled(seed)} LIMIT {limit}""")
    return [row["lemma"] for row in rows]


def sample_only_in(graph_pattern: str, limit: int, seed: int) -> list[str]:
    """Lemmas that appear in the matching graphs and in no other source."""
    rows = sparql(f"""
PREFIX ontolex: <http://www.w3.org/ns/lemon/ontolex#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX tlkg: <https://w3id.org/thailex/ontology/>
SELECT DISTINCT ?lemma WHERE {{
  GRAPH ?graph {{
    ?entry a ontolex:LexicalEntry ; rdfs:label ?lemma ; ontolex:sense ?sense .
    ?sense tlkg:hasDefinition/tlkg:definitionText ?definition .
  }}
  FILTER({graph_pattern})
  {_LEMMA_FILTER}
  FILTER(STRLEN(STR(?definition)) >= 25)
  FILTER NOT EXISTS {{
    GRAPH ?other {{ ?otherEntry a ontolex:LexicalEntry ; rdfs:label ?lemma ; ontolex:sense ?otherSense }}
    FILTER(!({graph_pattern.replace("?graph", "?other")}))
    {_SOURCE_FILTER.replace("?graph", "?other")}
  }}
}} {_shuffled(seed)} LIMIT {limit}""")
    return [row["lemma"] for row in rows]


def references_for(lemma: str) -> tuple[list[str], list[str]]:
    """Reference definitions and the source names that carry them."""
    entry = get_json(f"{BACKEND}/api/entry?q={urllib.parse.quote(lemma)}")
    definitions, sources = [], []
    for group in entry.get("groups", []):
        for sense in group["senses"]:
            for item in sense["candidate"]["evidence"]:
                if item["kind"] == "definition" and item.get("language") in (None, "th"):
                    definitions.append(item["text"])
                    sources.append(group["name"])
    return definitions, list(dict.fromkeys(sources))


def fake_words(real: list[str], count: int, rng: random.Random) -> list[str]:
    """Swap one consonant of a real word, keeping it pronounceable but not a word."""
    made = []
    for word in real:
        if len(made) >= count:
            break
        positions = [index for index, char in enumerate(word) if char in THAI_LETTER]
        if not positions:
            continue
        position = rng.choice(positions)
        candidate = word[:position] + rng.choice(THAI_LETTER.replace(word[position], "")) + word[position + 1:]
        if candidate not in made and not is_lemma(candidate):
            made.append(candidate)
    return made


def is_lemma(word: str) -> bool:
    """Exact label check, so a made-up word is not accepted because a fragment exists."""
    request = urllib.request.Request(
        GRAPHDB, data=urllib.parse.urlencode({"query": f"""
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
ASK {{ GRAPH ?graph {{ ?entry rdfs:label "{word}"@th }} }}"""}).encode(),
        headers={"Accept": "application/sparql-results+json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return bool(json.load(response)["boolean"])


# --- arms -------------------------------------------------------------------

def get_json(url: str, timeout: int = 240) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


MODEL_ONLY_SYSTEM = (
    "คุณเป็นผู้ช่วยด้านคำศัพท์ภาษาไทย ตอบสั้น ๆ ว่าคำที่ถูกถามมีความหมายว่าอะไร "
    "ถ้ามีหลายความหมายให้บอกความหมายหลัก ๆ ถ้าไม่รู้จักคำนั้นหรือไม่แน่ใจว่าเป็นคำในภาษาไทย "
    "ให้ตอบว่าไม่รู้จักคำนี้ ห้ามเดา"
)
PLAIN_DICT_SYSTEM = (
    "คุณเป็นผู้ช่วยด้านคำศัพท์ภาษาไทย ตอบว่าคำที่ถูกถามมีความหมายว่าอะไร "
    "โดยใช้เฉพาะรายการนิยามที่ให้มา ถ้ารายการว่างให้ตอบว่าไม่พบคำนี้ในข้อมูล"
)


def run_arm(arm: str, lemma: str, settings: dict[str, str]) -> dict:
    question = f"คำว่า {lemma} แปลว่าอะไร"
    if arm == "model-only":
        answer, seconds, error = thaillm(settings, MODEL_ONLY_SYSTEM, question)
        return {"answer": answer, "seconds": seconds, "error": error, "note": ""}
    if arm == "plain-dict":
        definitions, _ = references_for(lemma)
        listed = "\n".join(f"- {text}" for text in definitions[:6]) or "(ไม่มีนิยามในข้อมูล)"
        answer, seconds, error = thaillm(
            settings, PLAIN_DICT_SYSTEM, f"{question}\n\nรายการนิยามที่มี:\n{listed}"
        )
        return {"answer": answer, "seconds": seconds, "error": error, "note": f"{len(definitions[:6])} definitions"}
    if arm == "sarn-sap":
        started = time.time()
        request = urllib.request.Request(
            f"{BACKEND}/api/ask",
            data=json.dumps({"query": question, "max_candidates": 12}, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            return {"answer": "", "seconds": time.time() - started, "error": f"http_{exc.code}", "note": ""}
        diagnostics = result["diagnostics"]
        error = None
        if diagnostics["fallback_reason"] and "llm_" in diagnostics["fallback_reason"]:
            error = diagnostics["fallback_reason"].split(":")[0]
        return {
            "answer": result["answer"], "seconds": time.time() - started, "error": error,
            "note": f"{diagnostics['actual_selector']}/{diagnostics['answer_mode']}",
        }
    raise SystemExit(f"unknown arm {arm}")


JUDGE_SYSTEM = (
    "คุณเป็นผู้ตรวจคำตอบพจนานุกรมภาษาไทย จะได้รับนิยามอ้างอิงและคำตอบของผู้ช่วย "
    "ตัดสินว่าคำตอบระบุความหมายที่ตรงกับนิยามอ้างอิงข้อใดข้อหนึ่งหรือไม่ "
    "และมีข้อความที่ขัดกับนิยามอ้างอิงหรือไม่ "
    'ตอบ JSON เท่านั้น {"matches_reference": true|false, "contradicts_reference": true|false}'
)


def judge(settings: dict[str, str], answer: str, references: list[str]) -> dict[str, bool | str]:
    if not answer.strip():
        return {"matches_reference": "", "contradicts_reference": ""}
    blinded = CITATION.sub("", answer).strip()
    listed = "\n".join(f"- {text}" for text in references[:8]) or "(ไม่มีนิยามอ้างอิง)"
    content, _, error = thaillm(
        settings, JUDGE_SYSTEM, f"นิยามอ้างอิง:\n{listed}\n\nคำตอบของผู้ช่วย:\n{blinded}", max_tokens=700  # the thinking model needs room before the verdict
    )
    if error:
        return {"matches_reference": "", "contradicts_reference": ""}
    try:
        parsed = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
    except (AttributeError, json.JSONDecodeError):
        return {"matches_reference": "", "contradicts_reference": ""}
    return {
        "matches_reference": bool(parsed.get("matches_reference")),
        "contradicts_reference": bool(parsed.get("contradicts_reference")),
    }


def build_items(per_stratum: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    over = per_stratum * 2  # room to drop words whose entry carries no Thai definition
    pools = {
        "common": sample_common(over, seed),
        "rare": sample_only_in(f'?graph = <{GRAPH}/organizer/royal-dict/2542>', over, seed),
        "technical": sample_only_in(f'STRSTARTS(STR(?graph), "{GRAPH}/organizer/technical-")', over, seed),
        "dialect": sample_only_in(f'STRSTARTS(STR(?graph), "{GRAPH}/organizer/dialect-")', over, seed),
    }
    items = []
    for stratum, pool in pools.items():
        taken = 0
        for lemma in pool:
            if taken >= per_stratum:
                break
            if lemma in PROMPT_EXAMPLES:
                continue
            definitions, sources = references_for(lemma)
            if not definitions:
                continue
            taken += 1
            items.append({"stratum": stratum, "lemma": lemma, "references": definitions, "sources": sources})
    real = [item["lemma"] for item in items] or pools["common"]
    rng.shuffle(real)
    for lemma in fake_words(real, per_stratum, rng):
        items.append({"stratum": "fake", "lemma": lemma, "references": [], "sources": []})
    return items


def rejudge(path: Path, settings: dict[str, str]) -> list[dict]:
    """Score the answers of a finished run again, without asking them again."""
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    references: dict[str, list[str]] = {}
    for row in rows:
        if row["stratum"] == "fake" or row["error"] or not row["answer"].strip():
            continue
        if row["lemma"] not in references:
            references[row["lemma"]] = references_for(row["lemma"])[0]
        row.update(judge(settings, row["answer"], references[row["lemma"]]))
        print(f"  {row['stratum']:9} {row['lemma']:14} {row['arm']:11} "
              f"match={row['matches_reference']} contradicts={row['contradicts_reference']}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        row["seconds"] = float(row["seconds"])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-stratum", type=int, default=8)
    parser.add_argument("--arms", default="model-only,plain-dict,sarn-sap")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--judge", action="store_true", help="also score with ThaiLLM as judge (weak; read the CSV too)")
    parser.add_argument("--rejudge", metavar="CSV", help="judge the answers of a finished run instead of asking new ones")
    args = parser.parse_args()
    arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    settings = thaillm_settings()

    if args.rejudge:
        rows = rejudge(Path(args.rejudge), settings)
        print()
        report(rows, list(dict.fromkeys(row["arm"] for row in rows)))
        return 0

    items = build_items(args.per_stratum, args.seed)
    print(f"{len(items)} items: " + ", ".join(
        f"{stratum} {sum(1 for item in items if item['stratum'] == stratum)}"
        for stratum in dict.fromkeys(item["stratum"] for item in items)
    ))

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_path = ROOT / "reports" / f"bench-grounded-{stamp}.csv"
    out_path.parent.mkdir(exist_ok=True)
    rows = []
    with out_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "round", "stratum", "lemma", "arm", "seconds", "error", "note",
            "meaning_match", "abstained", "cited_source", "matches_reference",
            "contradicts_reference", "answer", "references",
        ])
        writer.writeheader()
        for round_number in range(1, args.repeat + 1):
            for item in items:
                # Arms run back to back so an outage hits them equally.
                for arm in arms:
                    result = run_arm(arm, item["lemma"], settings)
                    scored = judge(settings, result["answer"], item["references"]) if args.judge and item["references"] else {}
                    row = {
                        "round": round_number, "stratum": item["stratum"], "lemma": item["lemma"], "arm": arm,
                        "seconds": round(result["seconds"], 2), "error": result["error"] or "", "note": result["note"],
                        "meaning_match": "" if result["error"] or not item["references"] else int(meaning_match(result["answer"], item["references"])),
                        "abstained": "" if result["error"] else int(bool(ABSTAINED.search(result["answer"]))),
                        "cited_source": "" if result["error"] or not item["sources"] else int(any(name in result["answer"] for name in item["sources"])),
                        "matches_reference": scored.get("matches_reference", ""),
                        "contradicts_reference": scored.get("contradicts_reference", ""),
                        "answer": result["answer"].replace("\n", " ⏎ "),
                        "references": " | ".join(item["references"][:3]),
                    }
                    rows.append(row)
                    writer.writerow(row)
                    stream.flush()
                    print(f"  {row['stratum']:9} {row['lemma']:14} {arm:11} {row['seconds']:6.1f}s "
                          f"match={row['meaning_match']} abstain={row['abstained']} cite={row['cited_source']} "
                          f"{row['error']} {row['note']}")

    print(f"\nrows written to {out_path.relative_to(ROOT)}\n")
    report(rows, arms)
    return 0


def report(rows: list[dict], arms: list[str]) -> None:
    def rate(subset: list[dict], field: str) -> str:
        values = [int(row[field]) for row in subset if row[field] != ""]
        return f"{sum(values) / len(values):.0%} ({len(values)})" if values else "—"

    strata = list(dict.fromkeys(row["stratum"] for row in rows))
    print("| stratum | arm | meaning_match | abstained | cited_source | judge match | judge contradicts | median s | errors |")
    print("|---|---|---|---|---|---|---|---|---|")
    for stratum in strata:
        for arm in arms:
            subset = [row for row in rows if row["stratum"] == stratum and row["arm"] == arm]
            if not subset:
                continue
            times = sorted(row["seconds"] for row in subset)
            errors = sum(1 for row in subset if row["error"])
            print(f"| {stratum} | {arm} | {rate(subset, 'meaning_match')} | {rate(subset, 'abstained')} | "
                  f"{rate(subset, 'cited_source')} | {rate(subset, 'matches_reference')} | "
                  f"{rate(subset, 'contradicts_reference')} | {times[len(times) // 2]:.1f} | {errors}/{len(subset)} |")
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["arm"]].append(row)
    print("\nNotes: meaning_match is a content-word overlap proxy and favours answers that quote the dictionary.")
    print("For `fake` words, `abstained` is the score that matters: 1 means the arm refused to invent a meaning.")
    for arm, subset in grouped.items():
        kinds = defaultdict(int)
        for row in subset:
            if row["error"]:
                kinds[row["error"]] += 1
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items())) or "no failures"
        print(f"  {arm}: {len(subset)} calls, {detail} (failures excluded from the rates above)")
    print('"unfinished" means the reply ran out of tokens inside its own reasoning; "http_5xx" is a ThaiLLM outage.')


if __name__ == "__main__":
    sys.exit(main())
