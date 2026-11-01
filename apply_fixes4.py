"""Fourth patch: Threads-native formats. Run from repo root AFTER patches 1-3:  python apply_fixes4.py
  - mostly single punchy posts (short_insight 45%, mini_case_study 30%), threads only ~25% (no more forced thread days)
  - single-post prompt + schema (one post, hook line, blank line, 2-3 short sentences) instead of a 5-post schema
  - the unused reply-question feature is wired in (about 30% of posts end with a specific question)
  - the Game Boy 'gold standard' is shown only for threads (it was biasing every post)
Backups: *.bak4. Safe to re-run.
"""
from __future__ import annotations

import shutil
from pathlib import Path

GEN = [
    ("    pool_weights = [weights.get(n, 1.0) for n in names]\n    return random.choices(names, weights=pool_weights, k=1)[0].lower()",
     "    pool_weights = [float(f.get(\"weight\", 1.0)) * weights.get(n, 1.0) for f, n in zip(formats_config, names)]\n"
     "    return random.choices(names, weights=pool_weights, k=1)[0].lower()"),

    ('    feedback = ""\n    for attempt in range(1, max_attempts + 1):',
     '''    if not plan.is_threads_thread:
        schema_spec = re.sub(
            r'"threads_posts": \\[.*?\\n  \\]',
            lambda m: '"threads_posts": [\\n    "<ONE post only: hook line under 10 words, blank line, then 2-3 short sentences>"\\n  ]',
            schema_spec, flags=re.S,
        )
        structure_instruction = structure_instruction.split("GOLD STANDARD BENCHMARK")[0] + SINGLE_POST_GUIDE.format(
            limit=380 if plan.format == "short_insight" else 460)
    if plan.invite_reply_question:
        structure_instruction += REPLY_QUESTION_RULE
    feedback = ""
    for attempt in range(1, max_attempts + 1):'''),

    ("class GenerationFailed(RuntimeError):",
     '''SINGLE_POST_GUIDE = """
THREADS-NATIVE SINGLE POST (MANDATORY, overrides the 'thread' rules above):
- threads_posts must contain exactly ONE string, at most {limit} characters.
- Line 1 is the hook (under 10 words) and must stand alone. Then a blank line.
- Body is 2 or 3 short sentences in spoken rhythm. First: one concrete fact from the source, naming the company, product or person. Second: the trade-off or mechanism. Last: a takeaway that names no real company or person.
- It should read like an opinion with one fact inside, never like a summary. No lists, no hashtags, no emojis.
"""

REPLY_QUESTION_RULE = """
REPLY QUESTION (MANDATORY for this post): the final sentence is one specific question, under 14 words, that an operator can answer from their own work. It names no company or person. Never 'Thoughts?' or 'Agree?'.
"""


class GenerationFailed(RuntimeError):'''),
]

HOOKS = [('''formats:
  - id: "thread"
    name: "Thread"
    description: "Deep dive across 4 to 6 connected posts (each under 480 chars)."
    post_count: 4
    min_posts: 4
    max_posts: 6
    weight: 1.0
''', '''formats:
  - id: "short_insight"
    name: "Short insight"
    description: "One punchy post under 380 chars: hook, one fact, one lesson."
    post_count: 1
    weight: 0.45
  - id: "mini_case_study"
    name: "Mini case study"
    description: "One post under 460 chars: hook, a real company decision, the lesson."
    post_count: 1
    weight: 0.30
  - id: "thread"
    name: "Thread"
    description: "Deep dive across 4 to 6 connected posts (each under 480 chars)."
    post_count: 4
    min_posts: 4
    max_posts: 6
    weight: 0.25
''')]

SCHED_OLD = '''threads_rules:
  thursday:
    x_thread: true
    threads_thread: true
    min_posts: 4
    max_posts: 5
  tuesday:
    x_thread: false
    threads_thread: true
    min_posts: 4
    max_posts: 5
'''
SCHED = [(SCHED_OLD, "threads_rules: {}\n")]


def patch(path: str, reps: list[tuple[str, str]], marker: str) -> bool:
    p = Path(path)
    src = p.read_text(encoding="utf-8")
    if marker in src:
        print(f"[skip] {path} already patched")
        return True
    bad = [(old, src.count(old)) for old, _ in reps if src.count(old) != 1]
    if bad:
        print(f"[FAIL] {path}: anchors not found exactly once (nothing changed):")
        for old, n in bad:
            print(f"   found {n}x: {old.strip().splitlines()[0][:90]!r}")
        return False
    for old, new in reps:
        src = src.replace(old, new, 1)
    shutil.copy(p, p.with_suffix(p.suffix + ".bak4"))
    p.write_text(src, encoding="utf-8")
    print(f"[ok] patched {path}")
    return True


if __name__ == "__main__":
    ok = (patch("src/generate.py", GEN, "SINGLE_POST_GUIDE")
          and patch("config/hooks.yaml", HOOKS, "short_insight")
          and patch("config/schedule.yaml", SCHED, "threads_rules: {}"))
    raise SystemExit(0 if ok else 1)
