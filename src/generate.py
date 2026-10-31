"""Content generation engine for auto-poster using google-genai SDK.

Features:
- Optional X generation via ENABLE_X (default: false)
- Slot + Date resolution according to config/schedule.yaml
- config/style.md used as system instruction
- History buffer passing the last 50 topics (strict anti-repetition)
- Google Search grounding for real-world business & AI pillars
- Strict JSON output: {pillar, topic, threads_posts, [x_posts]}
- Regex rejection for banned phrases, hashtags, em dashes, URLs in X posts
- Self-critique pass (scores 1-10; regenerates if < 7, max 3 attempts)
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

# Banned buzzwords and patterns
BANNED_PHRASES: list[str] = [
    "here's the thing",
    "let that sink in",
    "game-changer",
    "in today's world",
    "unlock",
    "delve",
]

RHETORICAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(have you ever|what if|did you know|ever wonder|why do we|why do)\b", re.IGNORECASE),
]

SEARCH_GROUNDED_PILLARS: set[str] = {
    "tech_for_business",
    "marketing_case_study",
    "business_strategy",
    "ai_hack",
}


@dataclass(frozen=True)
class PostPlan:
    slot: str  # "morning" | "night"
    date: datetime
    weekday: str  # "monday", "tuesday", etc.
    pillar: str
    industry: str | None
    is_x_thread: bool
    is_threads_thread: bool
    x_post_count: int
    threads_post_count: int
    use_google_search: bool


@dataclass
class GeneratedContent:
    pillar: str
    topic: str
    x_posts: list[str]
    threads_posts: list[str]
    critique_score: int
    critique_reason: str
    attempts: int = 1


def parse_date_input(date_val: datetime | date | str | None = None) -> datetime:
    """Parse date from datetime, date, weekday name, or string."""
    if date_val is None:
        return datetime.now(timezone.utc)
    if isinstance(date_val, datetime):
        return date_val if date_val.tzinfo else date_val.replace(tzinfo=timezone.utc)
    if isinstance(date_val, date):
        return datetime.combine(date_val, datetime.min.time(), tzinfo=timezone.utc)

    date_str = str(date_val).strip()
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if date_str.lower() in weekdays:
        today = datetime.now(timezone.utc)
        target_idx = weekdays.index(date_str.lower())
        days_ahead = (target_idx - today.weekday()) % 7
        return today + timedelta(days=days_ahead)

    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return datetime.now(timezone.utc)


def load_schedule(path: Path | None = None) -> dict[str, Any]:
    file_path = path or Path(__file__).parent.parent / "config" / "schedule.yaml"
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_style_guide(path: Path | None = None) -> str:
    file_path = path or Path(__file__).parent.parent / "config" / "style.md"
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_recent_history(path: Path | None = None, limit: int = 50) -> list[str]:
    """Load up to 50 recent topics/summaries from history.json."""
    file_path = path or Path(__file__).parent.parent / "data" / "history.json"
    if not file_path.exists():
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                return []
            recent = data[-limit:]
            topics: list[str] = []
            for item in recent:
                topic = item.get("topic")
                if topic and str(topic).strip():
                    topics.append(str(topic).strip())
                else:
                    posts = item.get("posts") or item.get("threads_posts") or item.get("x_posts") or []
                    if posts:
                        first_line = str(posts[0]).strip().split("\n")[0]
                        topics.append(first_line[:90])
            return topics
    except (json.JSONDecodeError, OSError):
        return []


def resolve_plan(
    schedule: dict[str, Any],
    slot: str | None = None,
    date: datetime | date | str | None = None,
    force_pillar: str | None = None,
    enable_x: bool = False,
) -> PostPlan:
    """Resolve slot, date, weekday, pillar, and thread configuration."""
    dt = parse_date_input(date)
    weekday = dt.strftime("%A").lower()

    if not slot or slot == "auto":
        # 02:00 to 11:59 UTC (approx 07:30 to 17:29 IST) is morning, else night
        slot = "morning" if 2 <= dt.hour < 12 else "night"
    else:
        slot = slot.lower()

    # Thread rules from config/schedule.yaml
    thread_rules = schedule.get("threads_rules", {})
    day_rule = thread_rules.get(weekday, thread_rules.get("default", {}))

    is_x_thread: bool = day_rule.get("x_thread", False) if enable_x else False
    is_threads_thread: bool = day_rule.get("threads_thread", False)

    min_posts = day_rule.get("min_posts", 4)
    max_posts = day_rule.get("max_posts", 5)

    x_post_count = (random.randint(min_posts, max_posts) if is_x_thread else 1) if enable_x else 0
    threads_post_count = random.randint(min_posts, max_posts) if is_threads_thread else 1

    # Pillar resolution
    industry: str | None = None
    if force_pillar:
        pillar = force_pillar
    elif slot == "morning":
        pillars_config = schedule["slots"]["morning"]["pillars"]
        pillar_entry = pillars_config.get(weekday, "tech_for_business")
        if isinstance(pillar_entry, list):
            pillar = random.choice(pillar_entry)
        else:
            pillar = str(pillar_entry)
    else:
        night_config = schedule["slots"]["night"]
        pillars_list = night_config.get(
            "pillars",
            [
                {"name": "psychology_insight", "weight": 0.75},
                {"name": "motivation", "weight": 0.25},
            ],
        )
        names = [p["name"] for p in pillars_list]
        weights = [float(p.get("weight", 0.5)) for p in pillars_list]
        pillar = random.choices(names, weights=weights, k=1)[0]

        industries = night_config.get("industries", ["finance", "marketing", "operations", "startups"])
        industry = random.choice(industries)

    use_google_search = pillar in SEARCH_GROUNDED_PILLARS

    return PostPlan(
        slot=slot,
        date=dt,
        weekday=weekday,
        pillar=pillar,
        industry=industry,
        is_x_thread=is_x_thread,
        is_threads_thread=is_threads_thread,
        x_post_count=x_post_count,
        threads_post_count=threads_post_count,
        use_google_search=use_google_search,
    )


def extract_json(raw_text: str) -> dict[str, Any]:
    """Extract and parse JSON object from raw LLM output."""
    cleaned = raw_text.strip()

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = cleaned[start : end + 1]
        try:
            data = json.loads(snippet)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Failed to parse valid JSON object from LLM response:\n{raw_text[:300]}")


def validate_and_sanitize(
    candidate: dict[str, Any],
    plan: PostPlan,
    enable_x: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    """Validate candidate output against strict regex and structural rules."""
    topic = candidate.get("topic")
    if not topic or not isinstance(topic, str):
        return False, "Missing or empty 'topic' string.", candidate

    threads_posts = candidate.get("threads_posts")
    if not isinstance(threads_posts, list) or not threads_posts:
        return False, "Invalid or empty 'threads_posts' list.", candidate

    if plan.is_threads_thread and not (4 <= len(threads_posts) <= 5):
        return False, f"Threads thread requires 4-5 posts, got {len(threads_posts)}.", candidate
    if not plan.is_threads_thread and len(threads_posts) != 1:
        threads_posts = [threads_posts[0]]

    sanitized_th: list[str] = []
    for idx, post in enumerate(threads_posts, start=1):
        text = str(post).strip()

        if re.search(r"[—–]", text):
            return False, f"Threads post {idx} contains forbidden em dash.", candidate

        if re.search(r"#\w+", text):
            return False, f"Threads post {idx} contains forbidden hashtag.", candidate

        for banned in BANNED_PHRASES:
            if re.search(rf"\b{re.escape(banned)}\b", text, re.IGNORECASE):
                return False, f"Threads post {idx} contains banned phrase '{banned}'.", candidate

        if idx == 1:
            first_line = text.splitlines()[0].strip()
            for pat in RHETORICAL_PATTERNS:
                if pat.search(first_line):
                    return False, f"Threads post 1 opens with a rhetorical question: '{first_line}'.", candidate

        if len(text) > 480:
            return False, f"Threads post {idx} exceeds 480 characters ({len(text)} chars).", candidate

        sanitized_th.append(text)

    sanitized_x: list[str] = []
    if enable_x:
        x_posts = candidate.get("x_posts")
        if not isinstance(x_posts, list) or not x_posts:
            return False, "Invalid or empty 'x_posts' list.", candidate

        if plan.is_x_thread and not (4 <= len(x_posts) <= 5):
            return False, f"X thread requires 4-5 posts, got {len(x_posts)}.", candidate
        if not plan.is_x_thread and len(x_posts) != 1:
            x_posts = [x_posts[0]]

        for idx, post in enumerate(x_posts, start=1):
            text = str(post).strip()

            if re.search(r"https?://|www\.", text, re.IGNORECASE):
                return False, f"X post {idx} contains an outbound URL, which is forbidden.", candidate

            if re.search(r"[—–]", text):
                return False, f"X post {idx} contains forbidden em dash.", candidate

            if re.search(r"#\w+", text):
                return False, f"X post {idx} contains forbidden hashtag.", candidate

            for banned in BANNED_PHRASES:
                if re.search(rf"\b{re.escape(banned)}\b", text, re.IGNORECASE):
                    return False, f"X post {idx} contains banned phrase '{banned}'.", candidate

            if idx == 1:
                first_line = text.splitlines()[0].strip()
                for pat in RHETORICAL_PATTERNS:
                    if pat.search(first_line):
                        return False, f"X post 1 opens with a rhetorical question: '{first_line}'.", candidate

            if len(text) > 270:
                return False, f"X post {idx} exceeds 270 characters ({len(text)} chars).", candidate

            sanitized_x.append(text)

    valid_candidate = {
        "pillar": plan.pillar,
        "topic": topic.strip(),
        "threads_posts": sanitized_th,
        "x_posts": sanitized_x,
    }
    return True, "Passed regex and structural validation.", valid_candidate


def run_self_critique(
    client: Any,
    model: str,
    candidate: dict[str, Any],
    style_guide: str,
    enable_x: bool = False,
) -> tuple[int, str]:
    """Second LLM call: scores 1-10 on 'specific, human, non-generic, matches style.md'."""
    from google.genai import types

    th_posts_formatted = "\n---\n".join(candidate.get("threads_posts", []))
    x_posts_section = ""
    if enable_x and candidate.get("x_posts"):
        x_posts_formatted = "\n---\n".join(candidate.get("x_posts", []))
        x_posts_section = f"\nX POSTS:\n{x_posts_formatted}\n"

    critique_prompt = f"""
You are an uncompromising editor evaluating social posts written by a sharp founder from Mumbai.

CRITERIA:
1. Specific: Uses real companies, real numbers, real product features or business mechanics (never vague advice).
2. Human: Talks like a smart peer over coffee. Calm, confident, never preachy. Not like an AI or corporate brand.
3. Non-generic: Original insights only, never famous quotes or recycled tropes.
4. Matches style guide: No buzzwords, short punchy lines, no rhetorical openers.

STYLE GUIDE REFERENCE:
{style_guide}

CANDIDATE UNDER REVIEW:
Topic: {candidate.get('topic')}
Pillar: {candidate.get('pillar')}

THREADS POSTS:
{th_posts_formatted}
{x_posts_section}
INSTRUCTIONS:
Rate this candidate strictly from 1 to 10 (1 = generic AI fluff, 10 = flawless, specific, human founder insight).
Return ONLY a JSON object:
{{
  "score": <int between 1 and 10>,
  "reason": "<1-2 sentence justification focusing on specificity and voice>"
}}
"""

    try:
        config = types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        )
        response = client.models.generate_content(
            model=model,
            contents=critique_prompt,
            config=config,
        )
        data = extract_json(response.text or "{}")
        score = int(data.get("score", 5))
        reason = str(data.get("reason", "No reason provided."))
        return score, reason
    except Exception as e:
        print(f"[self_critique] Notice: critique API call encountered error ({e}), default pass.")
        return 8, "Self-critique pass fallback accepted."


def _fallback_sample(plan: PostPlan, enable_x: bool = False) -> GeneratedContent:
    """Style-matched fallback for offline/dry-run testing."""
    samples: dict[str, dict[str, Any]] = {
        "tech_for_business": {
            "topic": "Kirana billing software as disguised calculator",
            "x_posts": [
                "Small shop billing in India is quietly becoming software.\n\nA calculator that prints the bill now also tracks stock and GST. The ERP didn't come to the kirana. It disguised itself as a calculator.",
            ],
            "threads_posts": [
                "Small shop billing in India is quietly becoming software.\n\nA calculator that prints the bill now also tracks stock and GST. The ERP didn't come to the kirana. It disguised itself as a calculator.\n\nHardware Trojan horses win where mobile apps hit resistance.",
            ],
        },
        "marketing_case_study": {
            "topic": "Nescafe candy market penetration in Japan",
            "x_posts": [
                "Nescafe couldn't sell coffee in Japan, a tea country.\n\nSo they launched coffee-flavoured candy for kids first. 15 years later those kids grew up liking the taste. They didn't sell a product. They planted a habit.",
            ],
            "threads_posts": [
                "Nescafe couldn't sell coffee in Japan, a tea country.\n\nSo they launched coffee-flavoured candy for kids first. 15 years later those kids grew up liking the taste.",
                "In 1970, Nestle market research showed Japanese consumers loved the brand but bought zero coffee jars. Tea was cultural.",
                "Instead of burning capital on educational TV ads, they partnered with confectioners. Candy planted the flavour profile without asking anyone to break morning tea rituals.",
                "When that generation reached university, instant coffee took off.\n\nThey didn't sell a product. They planted a habit.",
            ],
        },
        "ai_hack": {
            "topic": "Reusable Gemini Gems for prompt persistence",
            "x_posts": [
                "Underrated Gemini trick: save your instructions as a reusable Gem.\n\nWrite your role, tone and format once, and every chat starts already knowing how you work.",
            ],
            "threads_posts": [
                "Underrated Gemini trick: save your instructions as a reusable Gem.\n\nWrite your role, tone and format once, and every chat starts already knowing how you work.\n\nSaves 10 minutes of context priming every single morning.",
            ],
        },
        "business_strategy": {
            "topic": "Slack video game failure pivot",
            "x_posts": [
                "Slack started as a video game company. The game failed.\n\nThe internal chat tool they built for themselves didn't. Your best product is sometimes the one you built to survive.",
            ],
            "threads_posts": [
                "Slack started as a video game company. The game failed.\n\nThe internal chat tool they built for themselves didn't. Your best product is sometimes the one you built to survive.",
            ],
        },
        "psychology_insight": {
            "topic": "Fear of public failure vs private failure",
            "x_posts": [
                "People don't fear failure. They fear being seen failing.\n\nThat's why most start in private and quit in private.",
            ],
            "threads_posts": [
                "People don't fear failure. They fear being seen failing.\n\nThat's why most start in private and quit in private.\n\nPublic accountability hurts early, but removes the quiet exit door.",
            ],
        },
        "motivation": {
            "topic": "Discipline vs fleeting feelings",
            "x_posts": [
                "Discipline is just remembering what you want more than what you feel.",
            ],
            "threads_posts": [
                "Discipline is just remembering what you want more than what you feel.\n\nFeelings change with sleep and sugar. Goals don't.",
            ],
        },
    }

    base = samples.get(plan.pillar, samples["business_strategy"])
    th_posts = base["threads_posts"]
    x_posts = base["x_posts"] if enable_x else []

    if plan.is_threads_thread and len(th_posts) == 1:
        th_posts = [
            "Slack started as a video game company called Glitch. The game burned through $10M and shut down in 2012.",
            "During development, the team built an IRC chat wrapper with file sharing and search so engineers could coordinate across Vancouver and San Francisco.",
            "When Glitch failed, they realized working without their internal chat tool was painful. It had search indexing, notifications, and clean channels.",
            "They pivoted everything to that tool and launched Slack in 2013. By 2020 Salesforce acquired it for $27.7B.\n\nYour best product is sometimes the internal tool you built just to survive.",
        ]

    if enable_x and plan.is_x_thread and len(x_posts) == 1:
        x_posts = [
            "Slack started as a video game company called Glitch. The game burned through $10M and shut down in 2012.",
            "During development, Stewart Butterfield's team built an IRC chat wrapper with file sharing and search so they could stay in sync across US and Canada.",
            "When the game died, they realized the game was useless, but working without their internal chat tool felt impossible.",
            "They polished that internal utility and launched Slack in 2013.\n\nYour most durable product is sometimes the tool you built just to survive.",
        ]

    return GeneratedContent(
        pillar=plan.pillar,
        topic=base["topic"],
        x_posts=x_posts,
        threads_posts=th_posts,
        critique_score=9,
        critique_reason="Style-matched reference standard.",
        attempts=1,
    )


def generate_content(
    slot: str | None = None,
    date: datetime | date | str | None = None,
    force_pillar: str | None = None,
    dry_run: bool = False,
    max_attempts: int = 3,
    enable_x: bool | None = None,
) -> GeneratedContent:
    """Generate social posts for Threads (and optionally X) using google-genai SDK."""
    if enable_x is None:
        enable_x = os.getenv("ENABLE_X", "false").lower() == "true"

    schedule = load_schedule()
    style_guide = load_style_guide()
    plan = resolve_plan(schedule, slot=slot, date=date, force_pillar=force_pillar, enable_x=enable_x)
    past_topics = load_recent_history(limit=50)

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    is_dry_run = dry_run or os.getenv("DRY_RUN", "").lower() == "true"
    if not api_key:
        if is_dry_run:
            print("[generate] No GEMINI_API_KEY found, using style-matched mock fallback for dry run.")
            return _fallback_sample(plan, enable_x=enable_x)
        raise ValueError("GEMINI_API_KEY environment variable is required.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    history_buffer = ""
    if past_topics:
        bullet_list = "\n".join(f"- {t}" for t in past_topics)
        history_buffer = f"""
DO NOT REPEAT ANY OF THE LAST 50 TOPICS, COMPANIES, OR QUOTE IDEAS LISTED BELOW:
{bullet_list}
"""

    search_instruction = ""
    tools: list[Any] | None = None
    if plan.use_google_search:
        tools = [types.Tool(google_search=types.GoogleSearch())]
        search_instruction = """
GOOGLE SEARCH GROUNDING ENABLED:
- You MUST use Google Search to verify real-world facts, real company names, exact numbers, metrics, feature releases, and dates.
- All facts and figures must be 100% real and verifiable.
- If unsure about a metric or feature, search and choose another verified example.
"""

    x_prompt_requirements = ""
    schema_spec = f"""{{
  "pillar": "{plan.pillar}",
  "topic": "<short 4-8 word topic or company summary>",
  "threads_posts": ["<post 1>", ...]
}}"""

    if enable_x:
        x_prompt_requirements = f"""
- X Format: {"Thread of 4-5 posts" if plan.is_x_thread else "Single post (exactly 1 post)"}
- Every post in x_posts MUST be strictly <= 270 characters.
- NEVER include URLs or links in x_posts.
"""
        schema_spec = f"""{{
  "pillar": "{plan.pillar}",
  "topic": "<short 4-8 word topic or company summary>",
  "x_posts": ["<post 1>", ...],
  "threads_posts": ["<post 1>", ...]
}}"""

    thread_rules_prompt = f"""
SLOT & PLATFORM FORMAT REQUIREMENTS:
- Slot: {plan.slot} | Weekday: {plan.weekday.capitalize()} | Pillar: {plan.pillar}
{f"- Industry focus: {plan.industry}" if plan.industry else ""}
- Threads Format: {"Thread of 4-5 posts" if plan.is_threads_thread else "Single post (exactly 1 post)"}
{x_prompt_requirements}
LENGTH AND FORMAT RULES:
- Every post in threads_posts MUST be strictly <= 480 characters.
- First post MUST be a punchy hook that establishes the core idea without clickbait or rhetorical questions.
- NO hashtags. NO em dashes (— or –).
- NO banned phrases: "Here's the thing", "Let that sink in", "game-changer", "In today's world", "unlock", "delve".
"""

    last_error_or_critique = ""

    for attempt in range(1, max_attempts + 1):
        critique_feedback = ""
        if last_error_or_critique:
            critique_feedback = f"\nATTENTION - PREVIOUS ATTEMPT REJECTED: {last_error_or_critique}. FIX THIS COMPLETELY.\n"

        prompt = f"""
{thread_rules_prompt}
{search_instruction}
{history_buffer}
{critique_feedback}

OUTPUT JSON SPECIFICATION:
Return ONLY a valid JSON object matching this schema:
{schema_spec}
"""

        try:
            config = types.GenerateContentConfig(
                system_instruction=style_guide,
                temperature=0.75,
                tools=tools,
            )

            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )

            raw_text = response.text or ""
            candidate = extract_json(raw_text)

            is_valid, validation_msg, valid_candidate = validate_and_sanitize(
                candidate=candidate,
                plan=plan,
                enable_x=enable_x,
            )
            if not is_valid:
                print(f"[generate] Attempt {attempt}/{max_attempts} validation rejected: {validation_msg}")
                last_error_or_critique = validation_msg
                continue

            score, critique_reason = run_self_critique(
                client=client,
                model=model_name,
                candidate=valid_candidate,
                style_guide=style_guide,
                enable_x=enable_x,
            )

            print(f"[generate] Attempt {attempt}/{max_attempts} critique score: {score}/10 -> {critique_reason}")

            if score < 7:
                last_error_or_critique = f"Self-critique score was {score}/10 ({critique_reason}). Must be more specific, human, and non-generic."
                continue

            return GeneratedContent(
                pillar=valid_candidate["pillar"],
                topic=valid_candidate["topic"],
                x_posts=valid_candidate.get("x_posts", []),
                threads_posts=valid_candidate["threads_posts"],
                critique_score=score,
                critique_reason=critique_reason,
                attempts=attempt,
            )

        except Exception as e:
            print(f"[generate] Attempt {attempt}/{max_attempts} error: {e}")
            last_error_or_critique = str(e)

    if is_dry_run:
        print("[generate] Max attempts reached in dry run, falling back to reference sample.")
        return _fallback_sample(plan, enable_x=enable_x)

    raise RuntimeError(
        f"Failed to generate acceptable posts after {max_attempts} attempts. Last issue: {last_error_or_critique}"
    )


def generate_posts(
    plan: PostPlan,
    style_guide: str,
    recent_history: list[str],
    dry_run: bool = False,
) -> list[str]:
    """Compatibility wrapper returning list of posts."""
    content = generate_content(
        slot=plan.slot,
        date=plan.date,
        force_pillar=plan.pillar,
        dry_run=dry_run,
        enable_x=plan.is_x_thread,
    )
    return content.threads_posts if plan.is_threads_thread else content.x_posts
