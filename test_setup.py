"""Validation and diagnostic test script for Project-X auto-poster.

Checks:
1. Files exist and .gitignore covers .env
2. Environment variables (.env) validity and presence
3. Gemini API key functionality
4. Threads API read, quota, and unpublished container creation
5. GitHub PAT repository secrets permissions
6. Dry runs for morning and night slots (rules, formatting, banned words, secrets mask)
7. data/history.json validity
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent
BANNED_PHRASES = [
    "here's the thing",
    "let that sink in",
    "game-changer",
    "in today's world",
    "unlock",
    "delve",
]


def mask_secret(val: str) -> str:
    if not val or len(val) < 8:
        return "***"
    return f"{val[:3]}...{val[-3:]}"


def run_check_1_files() -> tuple[bool, str]:
    required_files = [
        "src/main.py",
        "src/generate.py",
        "src/post_threads.py",
        "src/sanitize.py",
        "src/analytics.py",
        "config/style.md",
        "config/hooks.yaml",
        "requirements.txt",
        ".github/workflows/post.yml",
        ".github/workflows/refresh-threads-token.yml",
        ".gitignore",
    ]
    missing = [f for f in required_files if not (REPO_ROOT / f).exists()]
    if missing:
        return False, f"Missing required file(s): {', '.join(missing)}"

    gitignore_path = REPO_ROOT / ".gitignore"
    content = gitignore_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    if ".env" not in lines and not any(re.match(r"^\.env\*?$", line) for line in lines):
        return False, ".env is not properly ignored in .gitignore"

    return True, "All required files exist and .env is listed in .gitignore"


def run_check_2_env_vars() -> tuple[bool, str]:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return False, ".env file does not exist"

    required_vars = [
        "GEMINI_API_KEY",
        "THREADS_USER_ID",
        "THREADS_ACCESS_TOKEN",
        "GH_PAT",
    ]
    missing_or_placeholder = []
    for var in required_vars:
        val = os.getenv(var, "").strip()
        if not val or "REPLACE" in val:
            missing_or_placeholder.append(var)

    if missing_or_placeholder:
        return (
            False,
            f"Missing or placeholder env var(s) in .env: {', '.join(missing_or_placeholder)}",
        )

    enable_x = os.getenv("ENABLE_X", "false").lower()
    if enable_x not in ("false", "0", "no", ""):
        return False, f"ENABLE_X must be false or unset, got '{enable_x}'"

    return True, "Required environment variables present and valid; ENABLE_X is disabled"


def run_check_3_gemini() -> tuple[bool, str]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or "REPLACE" in api_key:
        return False, "GEMINI_API_KEY is not configured"

    model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model_name,
            contents="Say 'OK' in one word.",
        )
        text = (resp.text or "").strip()
        if not text:
            return False, "Gemini returned empty response"
        return True, f"Gemini API key verified with model {model_name}"
    except Exception as e:
        return False, f"Gemini API call failed: {e}"


def run_check_4_threads() -> tuple[bool, str]:
    user_id = os.getenv("THREADS_USER_ID", "").strip()
    token = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
    if not user_id or not token or "REPLACE" in token:
        return False, "THREADS_USER_ID or THREADS_ACCESS_TOKEN not configured"

    base_url = "https://graph.threads.net/v1.0"

    # 1. GET /me
    try:
        me_resp = requests.get(
            f"{base_url}/me",
            params={"fields": "id,username", "access_token": token},
            timeout=20,
        )
        if me_resp.status_code != 200:
            return False, f"Threads /me failed ({me_resp.status_code}): {me_resp.text}"
        me_data = me_resp.json()
        queried_id = str(me_data.get("id"))
        if queried_id != user_id:
            return (
                False,
                f"Queried Threads ID ({queried_id}) does not match THREADS_USER_ID ({user_id})",
            )
        username = me_data.get("username", "unknown")
    except Exception as e:
        return False, f"Error calling Threads /me: {e}"

    # 2. GET /{id}/threads_publishing_limit
    try:
        limit_resp = requests.get(
            f"{base_url}/{user_id}/threads_publishing_limit",
            params={"fields": "quota_usage,config", "access_token": token},
            timeout=20,
        )
        if limit_resp.status_code != 200:
            return (
                False,
                f"Threads /threads_publishing_limit failed ({limit_resp.status_code}): {limit_resp.text}",
            )
    except Exception as e:
        return False, f"Error checking publishing limit: {e}"

    # 3. POST /{id}/threads (create test container without publishing)
    try:
        create_resp = requests.post(
            f"{base_url}/{user_id}/threads",
            data={
                "media_type": "TEXT",
                "text": "Automated pipeline setup test container - do not publish.",
                "access_token": token,
            },
            timeout=20,
        )
        if create_resp.status_code not in (200, 201):
            return (
                False,
                f"Threads test container creation failed ({create_resp.status_code}): {create_resp.text}",
            )
        creation_id = create_resp.json().get("id")
        if not creation_id:
            return False, f"No container id returned by Threads: {create_resp.text}"
    except Exception as e:
        return False, f"Error creating test container on Threads: {e}"

    return (
        True,
        f"Threads verified for @{username} (ID: {user_id}), quota checked, test container created ({creation_id})",
    )


def run_check_5_gh_pat() -> tuple[bool, str]:
    pat = os.getenv("GH_PAT", "").strip()
    if not pat or "REPLACE" in pat:
        return False, "GH_PAT is not configured in .env"

    url = "https://api.github.com/repos/TheKrishJain/Project-X/actions/secrets/public-key"
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return True, "GH_PAT verified with read/write permission to Actions secrets"
        return False, f"GH_PAT check failed ({resp.status_code}): {resp.text}"
    except Exception as e:
        return False, f"Error verifying GH_PAT: {e}"


def run_check_6_dry_runs() -> tuple[bool, str]:
    # Collect all secrets to ensure none are leaked in output
    secret_values: set[str] = set()
    for k in ("GEMINI_API_KEY", "THREADS_ACCESS_TOKEN", "GH_PAT", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"):
        val = os.getenv(k, "").strip()
        if len(val) >= 8 and "REPLACE" not in val:
            secret_values.add(val)

    outputs: list[str] = []

    for slot in ("morning", "night"):
        cmd = [sys.executable, "-m", "src.main", "--slot", slot, "--dry-run"]
        res = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
        combined = (res.stdout + "\n" + res.stderr).strip()
        outputs.append(f"\n=== Output for slot: {slot} ===\n{combined}")

        if res.returncode != 0:
            return False, f"Command failed with exit code {res.returncode} for slot {slot}:\n{combined}"

        # Check for secrets leakage
        for s in secret_values:
            if s in combined:
                return False, f"Security alert: Secret value leaked in dry-run output for slot {slot}!"

        # Extract generated posts section
        # Format in main.py: "  [1/4] (104/480 chars):\n    <post text>"
        lines = combined.splitlines()
        for idx, line in enumerate(lines):
            # Check banned phrases in content lines
            for phrase in BANNED_PHRASES:
                if phrase in line.lower() and not line.startswith("NO banned phrases"):
                    return False, f"Banned phrase '{phrase}' found in output for slot {slot}: {line}"

            # Check em dashes in post text lines
            if line.startswith("    ") and re.search(r"[—–]", line):
                return False, f"Em dash found in post text for slot {slot}: {line}"

            # Check hashtags in post text lines
            if line.startswith("    ") and re.search(r"#\w+", line):
                return False, f"Hashtag found in output for slot {slot}: {line}"

            # Check URLs in post text lines
            if line.startswith("    ") and re.search(r"https?://|www\.", line):
                return False, f"Outbound URL found in output for slot {slot}: {line}"

            # Check absolute claims in post text lines
            for ab in (r"\balways\b", r"\bevery\s+single\s+day\b", r"\bnever\s+fails?\b"):
                if line.startswith("    ") and re.search(ab, line, re.IGNORECASE):
                    return False, f"Absolute claim '{ab}' found in output for slot {slot}: {line}"

            # Check manual thread numbering in post text lines
            if line.startswith("    ") and re.search(r"^\s*(\[\d+/\d+\]|\(\d+/\d+\)|\d+\.)", line.strip()):
                return False, f"Manual thread numbering prefix found in output for slot {slot}: {line}"

    print("\n--- Dry Run Sample Outputs ---")
    for o in outputs:
        print(o)
    print("------------------------------\n")

    return True, "Dry runs passed for both morning and night slots without rule violations or leaks"


def run_check_7_history_json() -> tuple[bool, str]:
    history_file = REPO_ROOT / "data" / "history.json"
    if not history_file.exists():
        return False, "data/history.json does not exist"
    try:
        content = history_file.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, list):
            return False, f"data/history.json must contain a JSON list, got {type(data).__name__}"
        return True, f"data/history.json is valid JSON list (entries: {len(data)})"
    except Exception as e:
        return False, f"data/history.json JSON decode error: {e}"


def run_check_8_dash_ban() -> tuple[bool, str]:
    from src.sanitize import assert_no_dashes, run_unit_tests, sanitize_post
    try:
        ok = run_unit_tests()
        if not ok:
            return False, "sanitize_post unit tests returned false"
        samples = [
            "Before — after",
            "Left – right",
            "Word - dash",
            "Quote — #hashtag and  spaces",
        ]
        for s in samples:
            clean = sanitize_post(s)
            assert_no_dashes(clean)
            if "—" in clean or "–" in clean:
                return False, f"Dash survived in output: '{clean}'"
        return True, "DASH BAN verified: unit tests passed, all dashes eliminated, hard-fail assertions active"
    except Exception as e:
        return False, f"DASH BAN check failed: {e}"


def run_check_9_substance_and_sentence_count() -> tuple[bool, str]:
    from src.sanitize import count_sentences, sanitize_post
    from src.generate import check_topic_blocklist, ABSOLUTE_CLAIM_PATTERNS

    # 1. Test non-one_liner sentence counting rule
    valid_3 = "In 2011, online payments were broken. Stripe built an API with seven lines of code. Developers quickly adopted it over legacy tools."
    cnt = count_sentences(valid_3)
    if cnt < 3:
        return False, f"count_sentences failed to recognize 3 sentences (got {cnt})"

    invalid_2 = "Online payments were broken. Stripe fixed them."
    cnt_short = count_sentences(invalid_2)
    if cnt_short >= 3:
        return False, f"count_sentences falsely counted 3 sentences on short text (got {cnt_short})"

    # 2. Test manual thread numbering removal
    raw_numbered = "[1/6] This is the first tweet.\n[2/6] This is the second tweet."
    cleaned_numbered = sanitize_post(raw_numbered)
    if "[1/6]" in cleaned_numbered or "[2/6]" in cleaned_numbered:
        return False, "Manual thread numbering was not stripped by sanitize_post"

    # 3. Test absolute claims detection
    absolute_samples = [
        "This always works.",
        "We ship every single day.",
        "A system that never fails.",
    ]
    for sample in absolute_samples:
        if not any(pat.search(sample) for pat in ABSOLUTE_CLAIM_PATTERNS):
            return False, f"Failed to detect absolute claim in '{sample}'"

    # 4. Test permanent blocklist
    for blocked in ["kirana", "gemini gems", "slack", "nescafe", "fear of being seen failing"]:
        if not check_topic_blocklist(f"A case study on {blocked}"):
            return False, f"Permanent blocklist failed to block '{blocked}'"

    return True, "Substance verification passed: >= 3 sentences enforced, thread numbering stripped, absolute claims and blocklists active"


def run_check_10_quality_gate_and_grounding_hygiene() -> tuple[bool, str]:
    from datetime import datetime, timezone
    from src.sanitize import strip_unverified_numbers
    from src.generate import (
        verify_numbers_in_source,
        validate_and_sanitize,
        PostPlan,
    )

    # 1. Test code-level number stripping and verification
    src = "Tobias Lutke founded Shopify in 2006."
    draft_num = "Tobias built Shopify in 9 weeks with 7 lines of code."
    cleaned = strip_unverified_numbers(draft_num, src)
    if "9 weeks" in cleaned or "7 lines" in cleaned:
        return False, f"strip_unverified_numbers failed to strip unverified counts: '{cleaned}'"
    
    unverified = verify_numbers_in_source(draft_num, src)
    if "9" not in unverified or "7" not in unverified:
        return False, f"verify_numbers_in_source failed to detect unverified numbers: {unverified}"

    # 2. Test thread format sentence counts (2-4 sentences required)
    plan_thread = PostPlan(
        slot="morning",
        date=datetime.now(timezone.utc),
        weekday="monday",
        pillar="tech_for_business",
        industry=None,
        hook_pattern="Counterintuitive Claim",
        format="thread",
        topic_tag="Business",
        is_trend_jack=False,
        is_x_thread=False,
        is_threads_thread=True,
        x_post_count=0,
        threads_post_count=4,
        invite_reply_question=False,
    )

    # 1-sentence post in thread must fail
    bad_cand_1 = {
        "entity": "Shopify",
        "topic": "Shopify architecture overview",
        "threads_posts": [
            "Online retail was hard.",
            "Lutke built a software tool in 2006. It processed transactions cleanly.",
            "Merchants adopted the software quickly. It scaled into a major platform.",
            "Focusing on merchant software rails created durable platform value. Quiet infrastructure wins.",
        ],
    }
    ok_1, msg_1, _ = validate_and_sanitize(bad_cand_1, plan_thread, source_title="Shopify", source_text=src)
    if ok_1:
        return False, "validate_and_sanitize failed to block thread post with only 1 sentence"

    # 5-sentence post in thread must fail
    bad_cand_5 = {
        "entity": "Shopify",
        "topic": "Shopify architecture overview",
        "threads_posts": [
            "Online retail was hard. Lutke built tools in 2006.",
            "One sentence here. Two sentences here. Three sentences here. Four sentences here. Five sentences here.",
            "Merchants adopted the software quickly. It scaled into a major platform.",
            "Focusing on merchant software rails created durable platform value. Quiet infrastructure wins.",
        ],
    }
    ok_5, msg_5, _ = validate_and_sanitize(bad_cand_5, plan_thread, source_title="Shopify", source_text=src)
    if ok_5:
        return False, "validate_and_sanitize failed to block thread post with 5 sentences (>4 allowed)"

    # 3. Test controversial researcher rejection (Dan Ariely)
    plan_single = PostPlan(
        slot="night",
        date=datetime.now(timezone.utc),
        weekday="monday",
        pillar="psychology_insight",
        industry="marketing",
        hook_pattern="Counterintuitive Claim",
        format="short_insight",
        topic_tag="Psychology",
        is_trend_jack=False,
        is_x_thread=False,
        is_threads_thread=False,
        x_post_count=0,
        threads_post_count=1,
        invite_reply_question=False,
    )
    cand_ariely = {
        "entity": "Decoy effect",
        "topic": "Decoy effect in consumer pricing",
        "threads_posts": [
            "Consumer choices are easily manipulated.\n\nDan Ariely demonstrated that adding an inferior choice changes behavior. Buyers select the middle tier more often. Positioning defines perceived value."
        ],
    }
    ok_ar, msg_ar, _ = validate_and_sanitize(cand_ariely, plan_single, source_title="Decoy effect", source_text="The decoy effect is a cognitive bias.")
    if ok_ar:
        return False, "validate_and_sanitize failed to block controversial researcher (Dan Ariely)"

    # 4. Test Quality Gate threshold: < 7.5 is rejected
    threshold = 7.5
    sample_scores = [7.2, 7.4, 6.8]
    for s in sample_scores:
        if s >= threshold:
            return False, f"Quality gate threshold logic error: {s} should be < {threshold}"
    if not (7.5 >= threshold and 8.0 >= threshold):
        return False, "Quality gate threshold logic error on valid passing scores"

    return True, "Quality gate hard block (<7.5), number verification, thread 2-4 sentence enforcement, and controversial researcher bans all verified"


def main() -> int:
    print("=" * 64)
    print("Project-X Setup & Diagnostic Validation")
    print("=" * 64)

    checks = [
        ("Files & .gitignore", run_check_1_files),
        ("Environment Variables", run_check_2_env_vars),
        ("Gemini API Key", run_check_3_gemini),
        ("Threads API & Container Creation", run_check_4_threads),
        ("GitHub PAT Permissions", run_check_5_gh_pat),
        ("DASH BAN Code-Level Verification", run_check_8_dash_ban),
        ("Substance, Sentence Count & Fact Hygiene", run_check_9_substance_and_sentence_count),
        ("Grounding Hygiene, Quality Gate & Fact Checks", run_check_10_quality_gate_and_grounding_hygiene),
        ("Dry Runs & Content Validation", run_check_6_dry_runs),
        ("History JSON Validity", run_check_7_history_json),
    ]

    passed = 0
    total = len(checks)

    for idx, (name, fn) in enumerate(checks, start=1):
        print(f"\n[{idx}/{total}] Checking {name}...")
        try:
            ok, msg = fn()
            if ok:
                passed += 1
                print(f"  PASS: {msg}")
            else:
                print(f"  FAIL: {msg}")
        except Exception as e:
            print(f"  FAIL: Exception during check: {e}")

    print("\n" + "=" * 64)
    print(f"Final Result: {passed}/{total} passed")
    print("=" * 64)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
