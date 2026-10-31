"""Main orchestration CLI for auto-poster.

Features:
- CLI args: --slot morning|night, --dry-run
- Random sleep 0-35 min for natural jitter (skipped on dry-run)
- Content generation via google-genai
- Independent dispatch to Threads and X (failure on one doesn't block the other)
- History recording in data/history.json & X budget tracking in data/x_usage.json
- Clear execution logging
- Exits non-zero (code 1) if both platforms fail, triggering GitHub Actions alert emails
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
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
    parser = argparse.ArgumentParser(description="Auto-poster for X and Threads")
    parser.add_argument(
        "--slot",
        choices=["morning", "night", "auto"],
        default=os.getenv("SLOT", "morning"),
        help="Posting slot: morning or night",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("DRY_RUN", "false").lower() == "true",
        help="Simulate execution without posting or sleeping",
    )
    # Optional flags for testing convenience
    parser.add_argument(
        "--date",
        default=os.getenv("DATE", None),
        help="Optional date override (e.g. 2026-10-31)",
    )
    parser.add_argument(
        "--day",
        default=os.getenv("FORCE_DAY", None),
        help="Optional weekday override (e.g. thursday)",
    )
    parser.add_argument(
        "--no-sleep",
        action="store_true",
        help="Skip jitter sleep even if not dry-run",
    )

    args = parser.parse_args()
    date_val = args.date or args.day

    # 1. Random jitter sleep: 0 to 35 minutes (0 to 2100 seconds)
    is_dry_run = args.dry_run
    if not is_dry_run and not args.no_sleep:
        jitter_seconds = random.randint(0, 35 * 60)
        minutes, seconds = divmod(jitter_seconds, 60)
        print(f"[main] Applying random jitter sleep: {jitter_seconds}s ({minutes}m {seconds}s)...")
        time.sleep(jitter_seconds)
    else:
        print("[main] Skipping jitter sleep (dry-run or no-sleep enabled).")

    # 2. Resolve plan
    schedule = load_schedule()
    plan = resolve_plan(
        schedule=schedule,
        slot=args.slot,
        date=date_val,
    )

    print("=" * 64)
    print(f"Auto-Poster Execution [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}]")
    print(f"  Slot:           {plan.slot.upper()}")
    print(f"  Date / Day:     {plan.date.strftime('%Y-%m-%d')} ({plan.weekday.capitalize()})")
    print(f"  Pillar:         {plan.pillar}")
    if plan.industry:
        print(f"  Industry:       {plan.industry}")
    print(f"  X format:       {'Thread (4-5 posts)' if plan.is_x_thread else 'Single Post'}")
    print(f"  Threads format: {'Thread (4-5 posts)' if plan.is_threads_thread else 'Single Post'}")
    print(f"  Dry run mode:   {is_dry_run}")
    print("=" * 64)

    # 3. Generate content via google-genai
    content = generate_content(
        slot=plan.slot,
        date=plan.date,
        force_pillar=plan.pillar,
        dry_run=is_dry_run,
    )

    print(f"\n[Generated Topic]: {content.topic}")
    print(f"[Self-Critique]:   Score {content.critique_score}/10 ({content.critique_reason})")

    print(f"\n--- X Posts ({len(content.x_posts)} item(s)) ---")
    for idx, post in enumerate(content.x_posts, start=1):
        print(f"  [{idx}/{len(content.x_posts)}] ({len(post)}/270 chars):\n    {post.replace(chr(10), chr(10) + '    ')}")

    print(f"\n--- Threads Posts ({len(content.threads_posts)} item(s)) ---")
    for idx, post in enumerate(content.threads_posts, start=1):
        print(f"  [{idx}/{len(content.threads_posts)}] ({len(post)}/480 chars):\n    {post.replace(chr(10), chr(10) + '    ')}")

    # 4. Dispatch independently to Threads and X
    x_success = False
    threads_success = False
    x_ids: list[str] = []
    threads_ids: list[str] = []

    # Dispatch to Threads
    print("\n" + "-" * 40)
    print(f"[Threads Dispatch] Posting {len(content.threads_posts)} item(s)...")
    try:
        threads_ids = post_to_threads(content.threads_posts, dry_run=is_dry_run)
        threads_success = True
        print(f"[Threads Dispatch] Succeeded! Post IDs: {threads_ids}")
    except Exception as e:
        print(f"[Threads Dispatch] FAILED: {e}", file=sys.stderr)

    # Dispatch to X
    print("\n" + "-" * 40)
    print(f"[X Dispatch] Posting {len(content.x_posts)} item(s)...")
    try:
        x_ids = post_to_x(content.x_posts, dry_run=is_dry_run)
        # If budget guard skipped X entirely, it still counts as cleanly handled
        x_success = True
        print(f"[X Dispatch] Succeeded! Tweet IDs: {x_ids}")
    except Exception as e:
        print(f"[X Dispatch] FAILED: {e}", file=sys.stderr)

    print("\n" + "=" * 64)

    # 5. Record to data/history.json
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
    print(f"[main] Recorded execution to {HISTORY_FILE.relative_to(HISTORY_FILE.parent.parent)}")

    # 6. Exit evaluation: exit non-zero if BOTH platforms failed
    if not threads_success and not x_success and not is_dry_run:
        print("[main] FATAL: Both Threads and X dispatches failed! Exiting with code 1.", file=sys.stderr)
        return 1

    print("[main] Execution completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
