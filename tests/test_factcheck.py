"""Fact gate tests. Run from repo root:
    python -m tests.test_factcheck          # offline (stub judge, no API calls)
    python -m tests.test_factcheck --live   # also runs the real Gemini judge on real Wikipedia text
"""
from __future__ import annotations

import json
import os
import sys

from src.factcheck import drop_failed_sentences, final_fact_gate, split_sentences

SRC = (
    "Liquid Death is an American brand of canned water. The company was founded by Mike Cessario, "
    "a former creative director. Its drinks are sold in tallboy aluminum cans. Its marketing uses heavy "
    "metal and punk rock aesthetics, promoting the message Kill Your Thirst and environmental initiatives "
    "such as Death to Plastic."
)
GB_SRC = (
    "The Game Boy is a handheld game console developed and manufactured by Nintendo. It was designed by "
    "Gunpei Yokoi. It used a monochrome reflective screen and prioritized battery life and affordability "
    "over color graphics, following a philosophy of using mature, low cost technology."
)


def stub(items):
    return lambda prompt: json.dumps(items)


def test_good_post_passes():
    posts = ["Liquid Death sells its water in tallboy aluminum cans. Its marketing uses heavy metal and punk rock aesthetics."]
    j = stub([
        {"i": 1, "type": "fact", "quote": "Its drinks are sold in tallboy aluminum cans.", "unsupported": []},
        {"i": 2, "type": "fact", "quote": "Its marketing uses heavy metal and punk rock aesthetics", "unsupported": []},
    ])
    g = final_fact_gate(posts, SRC, "Liquid Death", j)
    assert g.ok, g.report()


def test_fake_quote_rejected():
    posts = ["Liquid Death sells mountain water in cans and stores."]
    j = stub([{"i": 1, "type": "fact", "quote": "Liquid Death sells mountain water in cans.", "unsupported": []}])
    g = final_fact_gate(posts, SRC, "Liquid Death", j)
    assert not g.ok and "not found verbatim" in g.report(), g.report()


def test_offsets_computed_by_code_and_distinct():
    posts = ["Liquid Death sells water in tallboy aluminum cans. Mike Cessario founded the company."]
    j = stub([
        {"i": 1, "type": "fact", "quote": "Its drinks are sold in tallboy aluminum cans.", "unsupported": [], "offset": 0},
        {"i": 2, "type": "fact", "quote": "The company was founded by Mike Cessario", "unsupported": [], "offset": 0},
    ])
    g = final_fact_gate(posts, SRC, "Liquid Death", j)
    assert g.ok, g.report()
    offs = [r.offset for r in g.rows]
    assert len(set(offs)) == 2 and all(o > 0 for o in offs), offs  # model's fake offset=0 ignored
    for r in g.rows:
        assert r.source_slice == r.quote


def test_editorial_loophole_closed():
    posts = ["Liquid Death won nationwide shelf space without any media spend."]
    j = stub([{"i": 1, "type": "opinion", "quote": "", "unsupported": []}])  # model tries to wave it through
    g = final_fact_gate(posts, SRC, "Liquid Death", j)
    assert not g.ok and g.rows[0].kind == "fact", g.report()


def test_unsupported_detail_rejected():
    posts = ["Liquid Death packaged tap water in tallboy aluminum cans."]
    j = stub([{"i": 1, "type": "fact", "quote": "Its drinks are sold in tallboy aluminum cans.", "unsupported": ["tap water"]}])
    g = final_fact_gate(posts, SRC, "Liquid Death", j)
    assert not g.ok and "tap water" in g.report(), g.report()


def test_numbers_not_in_source_rejected():
    posts = ["The Game Boy ran for 30 hours on four batteries. Nintendo shipped it in 1989."]
    j = stub([
        {"i": 1, "type": "fact", "quote": "prioritized battery life and affordability over color graphics", "unsupported": []},
        {"i": 2, "type": "fact", "quote": "developed and manufactured by Nintendo", "unsupported": []},
    ])
    g = final_fact_gate(posts, GB_SRC, "Game Boy", j)
    bad = set(g.number_problems)
    assert not g.ok and {"30", "1989"} <= bad and any("four" in b for b in bad), g.report()


def test_opinion_lines_pass_without_quotes():
    posts = ["Most founders overbuild before they sell. Ship the smallest version and listen."]
    j = stub([{"i": 1, "type": "opinion", "quote": "", "unsupported": []},
              {"i": 2, "type": "opinion", "quote": "", "unsupported": []}])
    g = final_fact_gate(posts, GB_SRC, "Game Boy", j)
    assert g.ok, g.report()


def test_judge_garbage_fails_closed():
    g = final_fact_gate(["Liquid Death sells water in cans."], SRC, "Liquid Death", lambda p: "not json")
    assert not g.ok and "judge failed" in g.error


def test_missing_source_fails_closed():
    g = final_fact_gate(["Anything at all here."], "", "X", stub([]))
    assert not g.ok


def test_skipped_sentence_fails():
    posts = ["Liquid Death sells water in tallboy aluminum cans. Mike Cessario founded it."]
    j = stub([{"i": 1, "type": "fact", "quote": "Its drinks are sold in tallboy aluminum cans.", "unsupported": []}])
    g = final_fact_gate(posts, SRC, "Liquid Death", j)
    assert not g.ok, g.report()


def test_repair_drops_only_failed_sentences():
    posts = ["Water rarely looks like a heavy metal band. Liquid Death sells tap water in tallboy aluminum cans. "
             "Its marketing uses heavy metal and punk rock aesthetics. Packaging can carry a brand far."]
    j = stub([
        {"i": 1, "type": "opinion", "quote": "", "unsupported": []},
        {"i": 2, "type": "fact", "quote": "Its drinks are sold in tallboy aluminum cans.", "unsupported": ["tap water"]},
        {"i": 3, "type": "fact", "quote": "Its marketing uses heavy metal and punk rock aesthetics", "unsupported": []},
        {"i": 4, "type": "opinion", "quote": "", "unsupported": []},
    ])
    g = final_fact_gate(posts, SRC, "Liquid Death", j)
    fixed = drop_failed_sentences(posts, g)
    assert fixed and "tap water" not in fixed[0] and "punk rock" in fixed[0] and fixed[0].startswith("Water rarely"), fixed


def test_repair_refuses_when_hook_fails():
    posts = ["Liquid Death sells tap water in cans. Packaging can carry a brand far."]
    j = stub([{"i": 1, "type": "fact", "quote": "", "unsupported": ["tap water"]},
              {"i": 2, "type": "opinion", "quote": "", "unsupported": []}])
    g = final_fact_gate(posts, SRC, "Liquid Death", j)
    assert drop_failed_sentences(posts, g) is None


def test_repair_drops_unverified_numbers():
    posts = ["Handhelds were a crowded market. The Game Boy ran for 30 hours on batteries. "
             "It used a monochrome reflective screen. Boring choices can win."]
    j = stub([
        {"i": 1, "type": "opinion", "quote": "", "unsupported": []},
        {"i": 2, "type": "fact", "quote": "prioritized battery life and affordability over color graphics", "unsupported": []},
        {"i": 3, "type": "fact", "quote": "It used a monochrome reflective screen", "unsupported": []},
        {"i": 4, "type": "opinion", "quote": "", "unsupported": []},
    ])
    g = final_fact_gate(posts, GB_SRC, "Game Boy", j)
    fixed = drop_failed_sentences(posts, g)
    assert fixed and "30" not in fixed[0] and "monochrome" in fixed[0], fixed


def test_retry_wrapper_404_and_per_day_skip():
    from unittest.mock import patch
    from src.llm_retry import RetryingClient

    class FakeModels:
        def __init__(self, side_effects):
            self.side_effects = side_effects
            self.calls = []

        def generate_content(self, model, contents, config=None, **kw):
            self.calls.append(model)
            val = self.side_effects[model].pop(0)
            if isinstance(val, Exception):
                raise val
            return val

    class FakeClient:
        def __init__(self, side_effects):
            self.models = FakeModels(side_effects)

    # 1. 404 skip
    fake1 = FakeClient({
        "m-404": [Exception("404 NOT_FOUND. model is not found")],
        "m-ok": ["ok"],
    })
    sleeps = []
    with patch("time.sleep", side_effect=sleeps.append):
        c1 = RetryingClient(fake1, fallbacks=["m-ok"])
        res1 = c1.models.generate_content("m-404", "prompt")
    assert res1 == "ok" and fake1.models.calls == ["m-404", "m-ok"] and len(sleeps) == 0

    # 2. Per-day 429 skip without sleep
    fake2 = FakeClient({
        "m-daily": [Exception("429 RESOURCE_EXHAUSTED. Requests per day limit exceeded")],
        "m-ok": ["ok2"],
    })
    sleeps2 = []
    with patch("time.sleep", side_effect=sleeps2.append):
        c2 = RetryingClient(fake2, fallbacks=["m-ok"])
        res2 = c2.models.generate_content("m-daily", "prompt")
    assert res2 == "ok2" and fake2.models.calls == ["m-daily", "m-ok"] and len(sleeps2) == 0


def live_tests():
    from google import genai
    from src.factcheck import make_gemini_judge
    from src.generate import fetch_wikipedia_full_text

    from src.llm_retry import RetryingClient

    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    judge = make_gemini_judge(RetryingClient(genai.Client(api_key=os.environ["GEMINI_API_KEY"])), model)
    _, _, ld = fetch_wikipedia_full_text("Liquid Death")
    _, _, gb = fetch_wikipedia_full_text("Game Boy")
    assert ld and gb, "could not fetch Wikipedia text"
    cases = [
        (["Liquid Death packaged tap water like beer to kill plastic bottles."], ld, "Liquid Death"),
        (["Liquid Death secured nationwide retail shelf space without standard media spend."], ld, "Liquid Death"),
        (["The Game Boy was the first handheld with a color screen."], gb, "Game Boy"),
    ]
    for posts, src, ent in cases:
        g = final_fact_gate(posts, src, ent, judge)
        print(g.report())
        assert not g.error, f"LIVE: inconclusive, judge did not run ({g.error[:80]})"
        assert not g.ok, f"LIVE: false claim passed: {posts[0]}"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}\n{e}")
    if "--live" in sys.argv:
        try:
            live_tests()
            print("PASS live_tests")
        except AssertionError as e:
            fails += 1
            print(f"FAIL live_tests: {e}")
    print("ALL PASSED" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
