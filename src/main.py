"""Main orchestration CLI for auto-poster.

Resolves schedule, generates posts via google-genai, dispatches to X and Threads,
and records history and usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.generate import (
    generate_content,
    load_schedule,
    resolve_plan,
)
from src.post_threads import post_to_threads
from src.post_x import post_to_x

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_FILE = DATA_DIR / "history.json"


def record_history(
    plan_data: dict[str, Any],
    topic: str,
    x_posts: list[str],
    threads_posts: list[str],
    x_ids: list[str],
    threads_ids: list[str],
    critique_score: int,
    critique_reason: str,
    path: Path = HISTORY_FILE,
) -> None:
    """Append execution result to data/history.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []

    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):
                    history = content
        except (json.JSONDecodeError, OSError):
            history = []

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "slot": plan_data.get("slot"),
        "date": plan_data.get("date"),
        "weekday": plan_data.get("weekday"),
        "pillar": plan_data.get("pillar"),
        "industry": plan_data.get("industry"),
        "topic": topic,
        "critique_score": critique_score,
        "critique_reason": critique_reason,
        "is_x_thread": plan_data.get("is_x_thread"),
        "is_threads_thread": plan_data.get("is_threads_thread"),
        "x_posts": x_posts,
        "threads_posts": threads_posts,
        "x_post_ids": x_ids,
        "threads_post_ids": threads_ids,
    }

    history.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-poster for X and Threads using Gemini")
    parser.add_argument(
        "--slot",
        choices=["auto", "morning", "night"],
        default=os.getenv("SLOT", "auto"),
        help="Slot override (morning/night/auto)",
    )
    parser.add_argument(
        "--date",
        default=os.getenv("DATE", None),
        help="Date or ISO string (e.g. 2026-10-31)",
    )
    parser.add_argument(
        "--day",
        default=os.getenv("FORCE_DAY", None),
        help="Weekday override (e.g. monday, thursday)",
    )
    parser.add_argument(
        "--force-pillar",
        default=os.getenv("FORCE_PILLAR", None),
        help="Force a specific content pillar",
    )
    parser.add_argument(
        "--target",
        choices=["all", "x", "threads"],
        default="all",
        help="Target platform(s) to post",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("DRY_RUN", "false").lower() == "true",
        help="Simulate execution without calling live APIs",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate and preview posts only without posting or modifying data",
    )

    args = parser.parse_args()
    date_val = args.date or args.day

    schedule = load_schedule()
    plan = resolve_plan(
        schedule=schedule,
        slot=args.slot,
        date=date_val,
        force_pillar=args.force_pillar,
    )

    print("=" * 64)
    print(f"Auto-Poster Run [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}]")
    print(f"  Slot:              {plan.slot}")
    print(f"  Date / Weekday:    {plan.date.strftime('%Y-%m-%d')} ({plan.weekday.capitalize()})")
    print(f"  Pillar:            {plan.pillar}")
    if plan.industry:
        print(f"  Industry:          {plan.industry}")
    print(f"  Search Grounding:  {plan.use_google_search}")
    print(f"  X format:          {'Thread (4-5 posts)' if plan.is_x_thread else 'Single Post'}")
    print(f"  Threads format:    {'Thread (4-5 posts)' if plan.is_threads_thread else 'Single Post'}")
    print(f"  Dry run mode:      {args.dry_run or args.preview}")
    print("=" * 64)

    # 1. Generate content with google-genai
    content = generate_content(
        slot=plan.slot,
        date=plan.date,
        force_pillar=plan.pillar,
        dry_run=args.dry_run or args.preview,
    )

    print(f"\n[Topic]: {content.topic}")
    print(f"[Self-Critique Score]: {content.critique_score}/10 ({content.critique_reason})")

    print(f"\n--- X Content ({len(content.x_posts)} item(s)) ---")
    for i, p in enumerate(content.x_posts, start=1):
        print(f"X [{i}/{len(content.x_posts)}] ({len(p)}/270 chars):\n{p}\n")

    print(f"--- Threads Content ({len(content.threads_posts)} item(s)) ---")
    for i, p in enumerate(content.threads_posts, start=1):
        print(f"Threads [{i}/{len(content.threads_posts)}] ({len(p)}/480 chars):\n{p}\n")

    if args.preview:
        print("[preview] Preview complete. No posts dispatched.")
        return 0

    x_ids: list[str] = []
    threads_ids: list[str] = []

    # 2. Dispatch to X
    if args.target in ("all", "x"):
        print(f"\n[Dispatching to X ({len(content.x_posts)} post(s))]...")
        try:
            x_ids = post_to_x(content.x_posts, dry_run=args.dry_run)
            print(f"[X] Successfully posted: {x_ids}")
        except Exception as e:
            print(f"[X] Failed to post: {e}", file=sys.stderr)
            if not args.dry_run:
                pass

    # 3. Dispatch to Threads
    if args.target in ("all", "threads"):
        print(f"\n[Dispatching to Threads ({len(content.threads_posts)} post(s))]...")
        try:
            threads_ids = post_to_threads(content.threads_posts, dry_run=args.dry_run)
            print(f"[Threads] Successfully posted: {threads_ids}")
        except Exception as e:
            print(f"[Threads] Failed to post: {e}", file=sys.stderr)
            if not args.dry_run:
                pass

    # 4. Record to history
    plan_dict = {
        "slot": plan.slot,
        "date": plan.date.isoformat(),
        "weekday": plan.weekday,
        "pillar": plan.pillar,
        "industry": plan.industry,
        "is_x_thread": plan.is_x_thread,
        "is_threads_thread": plan.is_threads_thread,
    }
    record_history(
        plan_data=plan_dict,
        topic=content.topic,
        x_posts=content.x_posts,
        threads_posts=content.threads_posts,
        x_ids=x_ids,
        threads_ids=threads_ids,
        critique_score=content.critique_score,
        critique_reason=content.critique_reason,
    )
    print(f"\n[history] Run logged to {HISTORY_FILE.relative_to(HISTORY_FILE.parent.parent)}")
    print("\nRun completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
