"""Independent final fact gate. Runs on the FINAL post text before anything is published.

The LLM only *proposes* (sentence type, one supporting quote, unsupported details).
Code *decides*:
  - offsets are computed here, never taken from the model
  - every quote must exist verbatim in the fetched source text
  - a sentence that names a real entity, person, number or date is a FACT even if
    the model calls it "opinion" (closes the "editorial takeaway" loophole)
  - every number (digits, spelled-out, years) in the final text must be in the source
Fails closed on any error.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from src.sanitize import ALL_NUM_WORDS, _build_source_nums, _num_in_source

Judge = Callable[[str], str]

MIN_QUOTE_CHARS = 20
SOFT_NUM_WORDS = {"one", "first", "second", "half"}  # only checked when they quantify a unit
UNITS = (r"(?:hours?|days?|weeks?|months?|years?|decades?|minutes?|percent|million|billion|thousand|"
         r"hundred|times|users?|customers?|employees?|stores?|countries|units?|lines?|batteries|dollars?|rupees?)")
NUM_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, ALL_NUM_WORDS), key=len, reverse=True)) + r")\b(\s+" + UNITS + r"\b)?",
    re.IGNORECASE,
)
DIGIT_RE = re.compile(r"\d+(?:[.,]\d+)*")
GENERIC_CAPS = {"I", "AI", "API", "APIs", "CEO", "CEOs", "CTO", "CFO", "SaaS", "B2B", "B2C",
                "ERP", "UI", "UX", "LLM", "LLMs", "OK", "GST"}

JUDGE_PROMPT = """You are a strict fact-checker. Compare each SENTENCE with the SOURCE.

SOURCE:
<<<
{source}
>>>

SENTENCES:
{sentences}

For each sentence return an object:
- "i": the sentence number
- "type": "fact" if it says anything about a specific real company, person, product, event, study, number or date; "opinion" only for general advice that names nothing real.
- "quote": for facts, ONE contiguous excerpt copied character-for-character from SOURCE that supports it (20-300 chars). "" if none exists.
- "unsupported": every detail in the sentence that SOURCE does not state: names, numbers, dates, materials, actions, outcomes, motives, spending, comparisons. Paraphrase counts as supported only if the meaning is identical. [] if fully supported.

Return ONLY a JSON array with one object per sentence, in order."""


def normalize(text: str) -> str:
    text = (text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"')
            .replace("\u201d", '"').replace("\u00a0", " "))
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [normalize(p) for p in parts if len(p.strip().split()) >= 3]


def check_numbers(text: str, source: str) -> list[str]:
    """Numbers in text (digits, years, number words) that do not appear in source."""
    src_nums = _build_source_nums(source)
    flat = {n.replace(",", "") for n in src_nums}
    problems: set[str] = set()
    for m in DIGIT_RE.finditer(text):
        n = m.group(0)
        if not _num_in_source(n, src_nums) and n.replace(",", "") not in flat:
            problems.add(n)
    for m in NUM_WORD_RE.finditer(text):
        word, unit = m.group(1).lower(), m.group(2)
        if word in SOFT_NUM_WORDS and not unit:
            continue
        if not _num_in_source(word, src_nums):
            problems.add(m.group(0).strip())
    return sorted(problems)


def looks_factual(sentence: str, entity: str, source: str) -> list[str]:
    """Code-level reasons a sentence states a fact. Non-empty => needs a source quote."""
    reasons: list[str] = []
    if DIGIT_RE.search(sentence) or any(
        m.group(1).lower() not in SOFT_NUM_WORDS or m.group(2) for m in NUM_WORD_RE.finditer(sentence)
    ):
        reasons.append("contains a number")
    ent = re.sub(r"\s*\(.*?\)", "", entity or "").strip()
    if len(ent) >= 3 and ent.lower() in sentence.lower():
        reasons.append(f"mentions '{ent}'")
    src_words = set(re.findall(r"[A-Za-z][\w'-]*", source))
    lower_words = {w for w in src_words if w.islower()}
    names = []
    for idx, raw in enumerate(sentence.split()):
        w = raw.strip(".,;:!?\"'()[]")
        if not w or not w[0].isupper() or w in GENERIC_CAPS:
            continue
        if idx == 0 and not (w in src_words and w.lower() not in lower_words):
            continue  # ordinary capitalised first word
        names.append(w)
    if names:
        reasons.append("names " + ", ".join(dict.fromkeys(names)))
    return reasons


@dataclass
class Row:
    sentence: str
    kind: str
    reasons: list[str]
    quote: str = ""
    offset: int = -1
    source_slice: str = ""
    problems: list[str] = field(default_factory=list)


@dataclass
class GateResult:
    ok: bool
    rows: list[Row]
    number_problems: list[str]
    error: str = ""

    def as_table(self) -> list[dict[str, Any]]:
        return [{"claim": r.sentence, "kind": r.kind, "evidence": r.source_slice, "offset": r.offset,
                 "verified": not r.problems} for r in self.rows]

    def feedback(self) -> str:
        lines = ["PREVIOUS DRAFT FAILED FACT-CHECKING. Use ONLY facts stated in the SOURCE TEXT."]
        if self.error:
            lines.append(f"- {self.error}")
        if self.number_problems:
            lines.append(f"- Remove these numbers, they are not in the source: {', '.join(self.number_problems)}")
        for r in self.rows:
            if r.problems:
                lines.append(f"- \"{r.sentence}\": {'; '.join(r.problems)}. Rewrite it from the source or delete it.")
        return "\n".join(lines) + "\n"

    def report(self) -> str:
        out = [f"[factcheck] {'PASS' if self.ok else 'FAIL'}"]
        if self.error:
            out.append(f"  error: {self.error}")
        if self.number_problems:
            out.append(f"  numbers not in source: {self.number_problems}")
        for r in self.rows:
            tag = "ok  " if not r.problems else "FAIL"
            out.append(f"  [{tag}] ({r.kind}) {r.sentence}")
            if r.kind == "fact":
                out.append(f"         offset={r.offset} source_slice={r.source_slice[:160]!r}")
            for p in r.problems:
                out.append(f"         -> {p}")
        return "\n".join(out)


def _parse_items(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    if not isinstance(data, list):
        raise ValueError("judge did not return a JSON array")
    return [x for x in data if isinstance(x, dict)]


def final_fact_gate(posts: list[str], source_text: str, entity: str, judge: Judge) -> GateResult:
    src = normalize(source_text)
    if len(src) < 200:
        return GateResult(False, [], [], "source text missing or too short; refusing to publish")
    sentences = [s for p in posts for s in split_sentences(p)]
    if not sentences:
        return GateResult(False, [], [], "no sentences to check")
    numbers = check_numbers("\n".join(posts), src)

    try:
        prompt = JUDGE_PROMPT.format(
            source=src[:4000], sentences="\n".join(f"{i}. {s}" for i, s in enumerate(sentences, 1))
        )
        by_i = {int(x.get("i", 0)): x for x in _parse_items(judge(prompt))}
    except Exception as e:  # fail closed
        return GateResult(False, [], numbers, f"judge failed: {e}")

    rows: list[Row] = []
    for i, s in enumerate(sentences, 1):
        reasons = looks_factual(s, entity, src)
        row = Row(sentence=s, kind="fact" if reasons else "opinion", reasons=reasons)
        j = by_i.get(i)
        if j is None:
            row.problems.append("judge skipped this sentence")
            rows.append(row)
            continue
        if not reasons and str(j.get("type", "")).lower() == "fact":
            row.kind, row.reasons = "fact", ["judge marked it factual"]
        if row.kind == "fact":
            q = normalize(str(j.get("quote") or ""))
            if len(q) < MIN_QUOTE_CHARS:
                row.problems.append("no supporting quote from source")
            else:
                off = src.find(q)  # offset computed by code only
                if off < 0:
                    row.problems.append("quote not found verbatim in source")
                else:
                    sliced = src[off:off + len(q)]
                    assert sliced == q
                    row.quote, row.offset, row.source_slice = q, off, sliced
            unsupported = [str(u).strip() for u in (j.get("unsupported") or []) if str(u).strip()]
            if unsupported:
                row.problems.append("unsupported: " + "; ".join(unsupported))
        rows.append(row)

    ok = not numbers and all(not r.problems for r in rows)
    return GateResult(ok, rows, numbers)


def make_gemini_judge(client: Any, model: str) -> Judge:
    from google.genai import types

    cfg = types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json")

    def judge(prompt: str) -> str:
        return client.models.generate_content(model=model, contents=prompt, config=cfg).text or ""

    return judge


def drop_failed_sentences(posts: list[str], gate: GateResult) -> list[str] | None:
    """Repair: remove only the sentences that failed (and any with unverified numbers).
    Returns None when the repair isn't safe: the gate errored, the hook (first sentence) failed,
    or a post would be left empty. The result must still pass validation and the gate again."""
    if gate.ok or gate.error or not gate.rows:
        return None
    bad = {r.sentence for r in gate.rows if r.problems}
    bad |= {r.sentence for r in gate.rows
            if any(re.search(rf"(?<![\w.]){re.escape(n)}(?![\w])", r.sentence, re.I) for n in gate.number_problems)}
    if gate.rows[0].sentence in bad:
        return None
    out: list[str] = []
    for p in posts:
        kept = [s for s in split_sentences(p) if s not in bad]
        if not kept:
            return None
        out.append(" ".join(kept))
    return out
