"""Live checks of /api/ask against the running stack and imported data.

Unit tests use mocks; this script asks the real questions users typed, with
ThaiLLM and GraphDB, and checks the properties that matter: which word was
looked up, what kind of answer was given, that the model (not a fallback)
wrote it, and how long it took. Run it after any change to question handling:

    python scripts/eval-ask.py            # default http://127.0.0.1:8000
    python scripts/eval-ask.py --repeat 2 # catch unstable model output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

NOT_FOUND = "<not found>"
# (question, expected lemma, None for no word, NOT_FOUND for an unknown word;
#  allowed intents; previous turns in the same chat)
CASES: list[tuple[str, str | None, set[str], list[str]]] = [
    ("สวัสดี", None, {"conversation"}, []),
    ("ว่าไง ผมอยากถาม", None, {"conversation"}, []),
    ("ดาว", "ดาว", {"define_all"}, []),
    ("ผมอยากรู้ว่าคำว่า พ่อ เเปลว่าอะไร", "พ่อ", {"define_all"}, []),
    ("คำว่ากระดูกมีความหมายว่าอะไรได้บ้าง", "กระดูก", {"define_all"}, []),
    ("วิตกกังวล แปลว่าอะไร", "วิตกกังวล", {"define_all"}, []),
    ("คืนนี้ดาวสว่างมาก", "ดาว", {"define"}, []),
    ("เขาเป็นดาวของห้อง", "ดาว", {"define"}, []),
    ("พ่อขันนอตให้แน่น", "ขัน", {"define"}, []),
    ("รากศัพท์ของคำว่าไก่คือ", "ไก่", {"word_info"}, []),
    ("อ้วน เป็นลักษณะคำเเบบไหน", "อ้วน", {"word_info"}, []),
    ("คำว่าอ้วนออกเสียงยังไง", "อ้วน", {"word_info"}, []),
    ("คำพ้องของคำว่า สวย มีอะไรบ้าง", "สวย", {"related"}, []),
    ("เปรียบเทียบความหมายของคำว่า ขัน ในแต่ละพจนานุกรม", "ขัน", {"compare"}, []),
    ("คำว่าฟหกดหมายถึงอะไร", NOT_FOUND, {"define_all", "define"}, []),
    ("ไก่", "ไก่", {"define_all"}, ["รากศัพท์ของคำว่าไก่คือ"]),
    ("ไก่เเปลว่า", "ไก่", {"define_all"}, ["รากศัพท์ของคำว่าไก่คือ", "ไก่"]),
    ("แล้วคำนี้มีคำตรงข้ามไหม", "อ้วน", {"related"}, ["อ้วน แปลว่าอะไร"]),
]
LEAK = re.compile(r"\bS\d+(?:-E\d+)?\b|https?://")


def ask(base: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{base}/api/ask", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.load(response)


def run_case(base: str, question: str, lemma: str | None, intents: set[str], previous: list[str]) -> list[str]:
    history: list[dict] = []
    context_lemma = None
    context_sense = None
    for earlier in previous:
        result = ask(base, {"query": earlier, "history": history, "context_lemma": context_lemma,
                            "context_sense_uri": context_sense, "max_candidates": 12})
        history += [{"role": "user", "content": earlier}, {"role": "assistant", "content": result["answer"][:1200]}]
        context_lemma = result["detected_lemma"] or context_lemma
        context_sense = result["selection"]["selected_sense_uri"] or context_sense
    started = time.time()
    result = ask(base, {"query": question, "history": history, "context_lemma": context_lemma,
                        "context_sense_uri": context_sense, "max_candidates": 12})
    seconds = time.time() - started
    diagnostics = result["diagnostics"]
    problems = []
    if lemma == NOT_FOUND:
        if result["detected_lemma"] is not None or result["candidates"]:
            problems.append(f"unknown word resolved to {result['detected_lemma']!r}")
        if "ยังไม่พบ" not in result["answer"]:
            problems.append("answer does not say the word was not found")
    elif lemma is not None and result["detected_lemma"] != lemma:
        problems.append(f"lemma {result['detected_lemma']!r} != {lemma!r}")
    if result["detail"]["intent"] not in intents:
        problems.append(f"intent {result['detail']['intent']} not in {sorted(intents)}")
    if diagnostics["fallback_used"]:
        problems.append(f"fallback {diagnostics['fallback_reason']}")
    if lemma not in (None, NOT_FOUND) and diagnostics["answer_mode"] != "model":
        problems.append("answer not written by the model")
    if LEAK.search(result["answer"]):
        problems.append("internal id or URL in answer")
    if seconds > 15:
        problems.append(f"slow {seconds:.1f}s")
    status = "PASS" if not problems else "FAIL"
    print(f"{status} {seconds:5.1f}s  {question}  -> {result['detected_lemma']} / {result['detail']['intent']} / {diagnostics['answer_mode']}")
    for problem in problems:
        print(f"         - {problem}")
    print("         " + result["answer"][:220].replace("\n", " / "))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    failures = 0
    external = 0
    total = 0
    for round_number in range(1, args.repeat + 1):
        print(f"=== round {round_number}")
        for question, lemma, intents, previous in CASES:
            total += 1
            try:
                problems = run_case(args.base_url, question, lemma, intents, previous)
                failures += bool(problems)
                external += any(re.search(r"HTTPStatusError:5\d\d|Timeout|ConnectError", item) for item in problems)
            except (urllib.error.URLError, TimeoutError) as error:
                failures += 1
                print(f"FAIL        {question}  -> request error {error}")
    print(f"\n{total - failures}/{total} passed")
    if external:
        print(f"{external} failure(s) involved ThaiLLM server errors or timeouts (external API)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
