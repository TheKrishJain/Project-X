"""Content generation engine for auto-poster.

Resolves scheduled pillars, enforces style rules, and queries Gemini
to generate punchy single posts or threads.
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

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


@dataclass(frozen=True)
class PostPlan:
    slot: str  # "morning" | "night"
    weekday: str  # "monday", "tuesday", etc.
    pillar: str
    industry: str | None
    is_x_thread: bool
    is_threads_thread: bool
    num_posts: int  # 1 for single, 4-5 for thread


def load_schedule(path: Path | None = None) -> dict[str, Any]:
    file_path = path or Path(__file__).parent.parent / "config" / "schedule.yaml"
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_style_guide(path: Path | None = None) -> str:
    file_path = path or Path(__file__).parent.parent / "config" / "style.md"
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_recent_history(path: Path | None = None, limit: int = 12) -> list[str]:
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
                posts = item.get("posts", [])
                if posts:
                    # Capture first 80 characters of first post
                    first_line = posts[0].strip().split("\n")[0]
                    topics.append(first_line[:80])
            return topics
    except (json.JSONDecodeError, OSError):
        return []


def resolve_plan(
    schedule: dict[str, Any],
    slot: str | None = None,
    day: str | None = None,
    force_pillar: str | None = None,
    now: datetime | None = None,
) -> PostPlan:
    """Resolve slot, weekday, pillar, and thread configuration."""
    current_dt = now or datetime.now(timezone.utc)
    current_weekday = (day or current_dt.strftime("%A")).lower()

    # Determine slot if not explicitly provided
    if not slot or slot == "auto":
        # Morning window: 02:00 to 11:59 UTC (approx 07:30 to 17:29 IST)
        # Night window: 12:00 to 01:59 UTC (approx 17:30 to 07:29 IST)
        slot = "morning" if 2 <= current_dt.hour < 12 else "night"
    else:
        slot = slot.lower()

    # Determine thread rules by day
    thread_rules = schedule.get("threads_rules", {})
    day_thread_rule = thread_rules.get(current_weekday, thread_rules.get("default", {}))
    is_x_thread: bool = day_thread_rule.get("x_thread", False)
    is_threads_thread: bool = day_thread_rule.get("threads_thread", False)

    num_posts = 1
    if is_x_thread or is_threads_thread:
        min_p = day_thread_rule.get("min_posts", 4)
        max_p = day_thread_rule.get("max_posts", 5)
        num_posts = random.randint(min_p, max_p)

    # Determine pillar
    industry: str | None = None
    if force_pillar:
        pillar = force_pillar
    elif slot == "morning":
        pillars_config = schedule["slots"]["morning"]["pillars"]
        pillar_entry = pillars_config.get(current_weekday, "tech_for_business")
        if isinstance(pillar_entry, list):
            pillar = random.choice(pillar_entry)
        else:
            pillar = str(pillar_entry)
    else:
        # Night slot: psychology_insight 75%, motivation 25%
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

    return PostPlan(
        slot=slot,
        weekday=current_weekday,
        pillar=pillar,
        industry=industry,
        is_x_thread=is_x_thread,
        is_threads_thread=is_threads_thread,
        num_posts=num_posts,
    )


def clean_and_validate(text: str, max_chars: int = 280) -> str:
    """Enforce voice and formatting rules on generated text."""
    # Replace em-dashes and en-dashes
    cleaned = text.replace("—", " - ").replace("–", " - ")

    # Remove hashtags
    cleaned = re.sub(r"#\w+", "", cleaned)

    # Remove banned phrases (case-insensitive)
    for phrase in BANNED_PHRASES:
        cleaned = re.sub(re.escape(phrase), "", cleaned, flags=re.IGNORECASE)

    # Clean whitespace and strip
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if lines:
        for pat in RHETORICAL_PATTERNS:
            if pat.search(lines[0]):
                lines = lines[1:]
                break

    cleaned = "\n".join(lines).strip()
    # Normalize excessive spaces
    cleaned = re.sub(r" {2,}", " ", cleaned)

    # Truncate cleanly at sentence or line boundary if exceeding max_chars
    if len(cleaned) > max_chars:
        trimmed = cleaned[: max_chars - 3].rstrip()
        last_punct = max(trimmed.rfind("."), trimmed.rfind("\n"))
        if last_punct > 120:
            cleaned = trimmed[: last_punct + 1]
        else:
            cleaned = trimmed + "..."

    return cleaned


def _fallback_sample(plan: PostPlan) -> list[str]:
    """Curated fallbacks matching style.md for offline/dry-run testing."""
    samples = {
        "tech_for_business": [
            "Small shop billing in India is quietly becoming software.\n\nA calculator that prints the bill now also tracks stock and GST. The ERP didn't come to the kirana. It disguised itself as a calculator.",
        ],
        "marketing_case_study": [
            "Nescafe couldn't sell coffee in Japan, a tea country.\n\nSo they launched coffee-flavoured candy for kids first. 15 years later those kids grew up liking the taste. They didn't sell a product. They planted a habit.",
        ],
        "ai_hack": [
            "Underrated Gemini trick: save your instructions as a reusable Gem.\n\nWrite your role, tone and format once, and every chat starts already knowing how you work.",
        ],
        "business_strategy": [
            "Slack started as a video game company. The game failed.\n\nThe internal chat tool they built for themselves didn't. Your best product is sometimes the one you built to survive.",
        ],
        "psychology_insight": [
            "People don't fear failure. They fear being seen failing.\n\nThat's why most start in private and quit in private.",
        ],
        "motivation": [
            "Discipline is just remembering what you want more than what you feel.",
        ],
    }

    base = samples.get(plan.pillar, samples["business_strategy"])
    if plan.num_posts <= 1:
        return [clean_and_validate(base[0])]

    # Generate a 4-post thread for testing thread flows
    return [
        clean_and_validate(
            "Nescafe couldn't sell coffee in Japan, a tea country.\n\nSo they launched coffee-flavoured candy for kids first. 15 years later those kids grew up liking the taste."
        ),
        clean_and_validate(
            "In 1970, Nestle market research showed Japanese consumers loved the brand but simply bought zero coffee jars. Tea was cultural."
        ),
        clean_and_validate(
            "Instead of spending millions on educational TV ads, they partnered with confectioners. Candy introduced the flavour profile without asking anyone to break morning tea rituals."
        ),
        clean_and_validate(
            "When that generation reached university, instant coffee took off.\n\nThey didn't sell a product. They planted a habit."
        ),
    ][: plan.num_posts]


def generate_posts(
    plan: PostPlan,
    style_guide: str,
    recent_history: list[str],
    dry_run: bool = False,
) -> list[str]:
    """Generate social posts matching style rules via Gemini."""
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    if not api_key:
        if dry_run or os.getenv("DRY_RUN", "").lower() == "true":
            print("[generate] No GEMINI_API_KEY found, using style-matched mock fallback for dry run.")
            return _fallback_sample(plan)
        raise ValueError("GEMINI_API_KEY environment variable is missing.")

    history_text = "\n".join(f"- {t}" for t in recent_history[-8:]) if recent_history else "None"
    industry_cue = f"Industry context: {plan.industry}." if plan.industry else ""

    thread_instructions = ""
    if plan.num_posts > 1:
        thread_instructions = (
            f"You MUST generate exactly a thread of {plan.num_posts} connected posts.\n"
            "- Post 1 MUST be a punchy, standalone insight/hook that can stand alone as a single post.\n"
            "- Posts 2 through {plan.num_posts} expand with specific numbers, features, or company mechanics.\n"
            "- Each individual post MUST be strictly under 280 characters."
        )
    else:
        thread_instructions = (
            "You MUST generate exactly 1 single post.\n"
            "- It MUST be strictly under 280 characters."
        )

    prompt = f"""
You are the author described in the style guide below. Follow the tone, voice, and rules strictly.

STYLE GUIDE:
{style_guide}

TASK:
- Slot: {plan.slot}
- Weekday: {plan.weekday}
- Content Pillar: {plan.pillar}
{industry_cue}

RECENT POST TOPICS (DO NOT REPEAT THESE TOPICS OR COMPANIES):
{history_text}

THREAD REQUIREMENTS:
{thread_instructions}

CRITICAL RULES:
- Return ONLY a valid JSON array of strings: ["post 1", "post 2", ...]
- No hashtags. No em dashes (—). No rhetorical questions as opening lines.
- No banned buzzwords: "Here's the thing", "Let that sink in", "game-changer", "In today's world", "unlock", "delve".
- One idea per post. Short lines. Real company, real number, or real feature.
- Original insights only.
"""

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.7,
        )
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config,
        )

        response_text = response.text or "[]"
        parsed: Any = json.loads(response_text)
        if isinstance(parsed, dict) and "posts" in parsed:
            parsed = parsed["posts"]
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(f"Unexpected JSON format from LLM: {response_text}")

        cleaned_posts = [clean_and_validate(str(p)) for p in parsed if str(p).strip()]
        if not cleaned_posts:
            raise ValueError("All generated posts were empty after validation.")

        # Ensure correct count
        if plan.num_posts > 1 and len(cleaned_posts) < plan.num_posts:
            # If slightly short, pad or keep valid posts
            pass
        elif plan.num_posts == 1:
            cleaned_posts = [cleaned_posts[0]]

        return cleaned_posts

    except Exception as e:
        if dry_run or os.getenv("DRY_RUN", "").lower() == "true":
            print(f"[generate] API call error ({e}), using mock fallback for dry run.")
            return _fallback_sample(plan)
        raise
