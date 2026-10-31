"""Main orchestration CLI for auto-poster.

Resolves schedule, generates posts, dispatches to X and Threads,
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
    generate_posts,
    load_recent_history,
    load_schedule,
    load_style_guide,
    resolve_plan,
)
from src.post_threads import post_to_threads
from src.post_x import post_to_x

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_FILE = DATA_DIR / "history.json"


def record_history(
    plan_data: dict[str, Any],
    posts: list[str],
    x_ids: list[str],
    threads_ids: list[str],
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
        "weekday": plan_data.get("weekday"),
        "pillar": plan_data.get("pillar"),
        "industry": plan_data.get("industry"),
        "is_x_thread": plan_data.get("is_x_thread"),
        "is_threads_thread": plan_data.get("is_threads_thread"),
        "posts": posts,
        "x_post_ids": x_ids,
        "threads_post_ids": threads_ids,
    }

    history.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-poster for X and Threads")
    parser.add_argument(
        "--slot",
        choices=["auto", "morning", "night"],
        default=os.getenv("SLOT", "auto"),
        help="Slot override (morning/night/auto)",
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

    # 1. Load configuration and state
    schedule = load_schedule()
    style_guide = load_style_guide()
    recent_history = load_recent_history()

    # 2. Resolve plan for current slot & day
    plan = resolve_plan(
        schedule=schedule,
        slot=args.slot,
        day=args.day,
        force_pillar=args.force_pillar,
    )

    print("=" * 60)
    print(f"Auto-Poster Run [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}]")
    print(f"  Slot:           {plan.slot}")
    print(f"  Weekday:        {plan.weekday.capitalize()}")
    print(f"  Pillar:         {plan.pillar}")
    if plan.industry:
        print(f"  Industry:       {plan.industry}")
    print(f"  X format:       {'Thread' if plan.is_x_thread else 'Single Post'}")
    print(f"  Threads format: {'Thread' if plan.is_threads_thread else 'Single Post'}")
    print(f"  Total planned:  {plan.num_posts} post(s)")
    print(f"  Dry run mode:   {args.dry_run}")
    print("=" * 60)

    # 3. Generate content
    posts = generate_posts(
        plan=plan,
        style_guide=style_guide,
        recent_history=recent_history,
        dry_run=args.dry_run or args.preview,
    )

    print(f"\n[Generated {len(posts)} post(s)]")
    for i, p in enumerate(posts, start=1):
        print(f"--- Post {i} ({len(p)} chars) ---\n{p}\n")

    if args.preview:
        print("[preview] Preview complete. No posts dispatched.")
        return 0

    # 4. Route posts to platforms according to schedule rules
    # Thu: thread on both
    # Tue/Sat: thread on Threads only, single post on X
    # Other: single post on both
    x_posts = posts if plan.is_x_thread else [posts[0]]
    threads_posts = posts if plan.is_threads_thread else [posts[0]]

    x_ids: list[str] = []
    threads_ids: list[str] = []

    # 5. Dispatch to X
    if args.target in ("all", "x"):
        print(f"\n[Dispatching to X ({len(x_posts)} post(s))]...")
        try:
            x_ids = post_to_x(x_posts, dry_run=args.dry_run)
            print(f"[X] Successfully posted: {x_ids}")
        except Exception as e:
            print(f"[X] Failed to post: {e}", file=sys.stderr)
            if not args.dry_run:
                # Do not abort threads if X fails, continue or raise
                pass

    # 6. Dispatch to Threads
    if args.target in ("all", "threads"):
        print(f"\n[Dispatching to Threads ({len(threads_posts)} post(s))]...")
        try:
            threads_ids = post_to_threads(threads_posts, dry_run=args.dry_run)
            print(f"[Threads] Successfully posted: {threads_ids}")
        except Exception as e:
            print(f"[Threads] Failed to post: {e}", file=sys.stderr)
            if not args.dry_run:
                pass

    # 7. Record to history if at least one post was made (or in dry run)
    if not args.preview:
        plan_dict = {
            "slot": plan.slot,
            "weekday": plan.weekday,
            "pillar": plan.pillar,
            "industry": plan.industry,
            "is_x_thread": plan.is_x_thread,
            "is_threads_thread": plan.is_threads_thread,
        }
        record_history(plan_dict, posts, x_ids, threads_ids)
        print(f"\n[history] Run logged to {HISTORY_FILE.relative_to(HISTORY_FILE.parent.parent)}")

    print("\nRun completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
