"""Main orchestration CLI for auto-poster.

Features:
- CLI args: --slot morning|night, --dry-run, --enable-x, --now, --run-analytics
- Self-Improving Growth System orchestration
- Random sleep 0-35 min for natural jitter (skipped on dry-run, --now, --no-sleep)
- Content generation via google-genai with hook & format optimization
- Enforces strict DASH BAN (sanitize_post + assert_no_dashes)
- Independent dispatch to Threads with topic_tag support
- Feedback loop: daily performance metrics sync, Sunday dynamic weight recomputation & weekly reports
- History recording in data/history.json
- Exits non-zero (code 1) on complete failure, triggering GitHub Actions alert emails
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

from src.analytics import (
    generate_weekly_report,
    sync_performance_metrics,
    update_dynamic_weights,
)
from src.generate import (
    generate_content,
    load_schedule,
    resolve_plan,
)
from src.post_threads import get_post_details, post_to_threads
from src.post_x import post_to_x
from src.sanitize import assert_no_dashes, sanitize_post

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_FILE = DATA_DIR / "history.json"


def record_history(
    plan_data: dict[str, Any],
    topic: str,
    entity: str,
    hook_pattern: str,
    format_type: str,
    topic_tag: str,
    is_trend_jack: bool,
    x_posts: list[str],
    threads_posts: list[str],
    x_ids: list[str],
    threads_ids: list[str],
    critique_score: float,
    critique_reason: str,
    path: Path = HISTORY_FILE,
) -> None:
    """Append execution result to data/history.json with growth engine attributes."""
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
        "entity": entity,
        "topic": topic,
        "hook_pattern": hook_pattern,
        "format": format_type,
        "topic_tag": topic_tag.lstrip("#"),
        "is_trend_jack": is_trend_jack,
        "critique_score": critique_score,
        "critique_reason": critique_reason,
        "is_x_thread": plan_data.get("is_x_thread", False),
        "is_threads_thread": plan_data.get("is_threads_thread", False),
        "x_posts": x_posts,
        "threads_posts": threads_posts,
        "x_post_ids": x_ids,
        "threads_post_ids": threads_ids,
    }

    history.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-Improving Auto-Poster for Threads (and optionally X)")
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
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live posting mode regardless of DRY_RUN setting",
    )
    parser.add_argument(
        "--draft-file",
        default=None,
        help="Optional path to a pre-approved draft JSON to publish directly",
    )
    parser.add_argument(
        "--enable-x",
        action="store_true",
        default=os.getenv("ENABLE_X", "false").lower() == "true",
        help="Enable X generation and posting (default: false)",
    )
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
    parser.add_argument(
        "--now",
        action="store_true",
        help="Skip jitter sleep and publish immediately (alias for --no-sleep)",
    )
    parser.add_argument(
        "--run-analytics",
        action="store_true",
        help="Synchronize engagement metrics, update weights, and generate weekly report",
    )

    args = parser.parse_args()
    date_val = args.date or args.day
    enable_x = args.enable_x
    is_dry_run = args.dry_run and not args.live
    skip_sleep = is_dry_run or args.no_sleep or args.now

    # 0. Standalone analytics execution if requested
    if args.run_analytics:
        print("[main] Running analytics cycle...")
        sync_performance_metrics()
        update_dynamic_weights()
        generate_weekly_report()
        return 0

    # 1. Random jitter sleep: 0 to 35 minutes (skipped on dry-run, --no-sleep, or --now)
    if not skip_sleep:
        jitter_seconds = random.randint(0, 35 * 60)
        minutes, seconds = divmod(jitter_seconds, 60)
        print(f"[main] Applying random jitter sleep: {jitter_seconds}s ({minutes}m {seconds}s)...")
        time.sleep(jitter_seconds)
    else:
        print("[main] Skipping jitter sleep (dry-run, --no-sleep, or --now enabled).")

    # 2. Content Generation or Pre-approved Draft Loading
    if args.draft_file:
        print(f"[main] Loading pre-approved draft from {args.draft_file}...")
        with open(args.draft_file, "r", encoding="utf-8") as f:
            d_data = json.load(f)
        from src.generate import GeneratedContent
        content = GeneratedContent(
            pillar=d_data.get("pillar", "psychology_insight"),
            topic=d_data.get("topic", "The IKEA Effect in operations"),
            hook_pattern=d_data.get("hook_pattern", "most_people_think"),
            format=d_data.get("format", "thread"),
            topic_tag=d_data.get("topic_tag", "Psychology"),
            is_trend_jack=d_data.get("is_trend_jack", False),
            x_posts=[],
            threads_posts=d_data.get("threads_posts", []),
            critique_score=float(d_data.get("critique_score", 8.0)),
            critique_details=d_data.get("critique_details", {}),
            critique_reason=d_data.get("critique_reason", "Pre-approved growth draft."),
            attempts=1,
        )
    else:
        content = generate_content(
            slot=args.slot,
            date=date_val,
            dry_run=is_dry_run,
            enable_x=enable_x,
        )

    # 3. Code-level DASH BAN Verification
    sanitized_th: list[str] = []
    for p in content.threads_posts:
        clean = sanitize_post(p)
        assert_no_dashes(clean)
        sanitized_th.append(clean)
    content.threads_posts = sanitized_th

    sanitized_x: list[str] = []
    for p in content.x_posts:
        clean = sanitize_post(p)
        assert_no_dashes(clean)
        sanitized_x.append(clean)
    content.x_posts = sanitized_x

    print("=" * 64)
    print(f"Self-Improving Auto-Poster [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}]")
    print(f"  Slot:           {args.slot.upper()}")
    print(f"  Pillar:         {content.pillar}")
    print(f"  Entity:         {content.entity}")
    print(f"  Hook Pattern:   {content.hook_pattern}")
    print(f"  Format:         {content.format}")
    print(f"  Topic Tag:      {content.topic_tag.lstrip('#')}")
    print(f"  Trend-Jack:     {content.is_trend_jack}")
    print(f"  Quality Score:  {content.critique_score}/10")
    print(f"  Runtime:        {content.runtime_seconds}s")
    print(f"  Threads Posts:  {len(content.threads_posts)} item(s)")
    print(f"  X Enabled:      {enable_x}")
    print(f"  Dry run mode:   {is_dry_run}")
    print("=" * 64)

    print(f"\n[Generated Topic]: {content.topic}")
    print(f"[Quality Gate]:    Score {content.critique_score}/10 ({content.critique_reason})")
    if content.critique_details:
        print(f"  Criteria Breakdown: {content.critique_details}")

    if content.grounding_sources:
        print(f"\n[Grounding Sources ({len(content.grounding_sources)})]:")
        for s in content.grounding_sources:
            print(f"  - Title: '{s['title']}' | PubDate: {s.get('date')} | URL: {s.get('url')}")

    print(f"\n--- Threads Posts ({len(content.threads_posts)} item(s) | Topic Tag: {content.topic_tag.lstrip('#')}) ---")
    for idx, post in enumerate(content.threads_posts, start=1):
        print(f"  [{idx}/{len(content.threads_posts)}] ({len(post)}/480 chars):\n    {post.replace(chr(10), chr(10) + '    ')}")

    if enable_x and content.x_posts:
        print(f"\n--- X Posts ({len(content.x_posts)} item(s)) ---")
        for idx, post in enumerate(content.x_posts, start=1):
            print(f"  [{idx}/{len(content.x_posts)}] ({len(post)}/270 chars):\n    {post.replace(chr(10), chr(10) + '    ')}")

    # 4. Dispatch to platforms
    threads_success = False
    threads_ids: list[str] = []

    clean_topic_tag = content.topic_tag.lstrip("#")
    print("\n" + "-" * 40)
    print(f"[Threads Dispatch] Posting {len(content.threads_posts)} item(s) (tag: {clean_topic_tag})...")
    try:
        threads_ids = post_to_threads(
            posts=content.threads_posts,
            dry_run=is_dry_run,
            topic_tag=clean_topic_tag,
        )
        threads_success = True
        print(f"[Threads Dispatch] Succeeded! Post IDs: {threads_ids}")
        if threads_ids and not is_dry_run:
            details = get_post_details(threads_ids[0])
            permalink = details.get("permalink")
            if permalink:
                print(f"[Threads Dispatch] Live Permalink: {permalink}")
    except Exception as e:
        print(f"[Threads Dispatch] FAILED: {e}", file=sys.stderr)

    x_success = False
    x_ids: list[str] = []

    if enable_x and content.x_posts:
        print("\n" + "-" * 40)
        print(f"[X Dispatch] Posting {len(content.x_posts)} item(s)...")
        try:
            x_ids = post_to_x(content.x_posts, dry_run=is_dry_run, enable_x=enable_x)
            x_success = True
            print(f"[X Dispatch] Succeeded! Tweet IDs: {x_ids}")
        except Exception as e:
            print(f"[X Dispatch] FAILED: {e}", file=sys.stderr)
    else:
        print("\n[X Dispatch] Skipped (ENABLE_X is false).")

    print("\n" + "=" * 64)

    # 5. Record to data/history.json
    plan_dict = {
        "slot": args.slot,
        "date": datetime.now(timezone.utc).isoformat(),
        "weekday": datetime.now(timezone.utc).strftime("%A").lower(),
        "pillar": content.pillar,
        "industry": getattr(content, "industry", None),
        "is_x_thread": False,
        "is_threads_thread": (len(content.threads_posts) > 1),
    }
    record_history(
        plan_data=plan_dict,
        topic=content.topic,
        entity=content.entity,
        hook_pattern=content.hook_pattern,
        format_type=content.format,
        topic_tag=clean_topic_tag,
        is_trend_jack=content.is_trend_jack,
        x_posts=content.x_posts if enable_x else [],
        threads_posts=content.threads_posts,
        x_ids=x_ids if enable_x else [],
        threads_ids=threads_ids,
        critique_score=content.critique_score,
        critique_reason=content.critique_reason,
    )
    print(f"[main] Recorded execution to {HISTORY_FILE.relative_to(HISTORY_FILE.parent.parent)}")

    # 6. Analytics Sync & Weekly Optimization Loop
    try:
        sync_performance_metrics()
        # Weekly recomputation on Sundays
        if datetime.now(timezone.utc).weekday() == 6:  # Sunday
            print("[main] Sunday optimization routine: updating weights and writing weekly report...")
            update_dynamic_weights()
            generate_weekly_report()
    except Exception as e:
        print(f"[main] Analytics cycle notice: {e}")

    # 7. Exit evaluation: exit non-zero on failure
    if enable_x:
        if not threads_success and not x_success and not is_dry_run:
            print("[main] FATAL: Both Threads and X dispatches failed! Exiting with code 1.", file=sys.stderr)
            return 1
    else:
        if not threads_success and not is_dry_run:
            print("[main] FATAL: Threads dispatch failed! Exiting with code 1.", file=sys.stderr)
            return 1

    print("[main] Execution completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
