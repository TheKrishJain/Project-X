"""X (Twitter) API v2 posting integration using tweepy.Client.

Features:
- ENABLE_X guard: when false (default), skips X completely, requires zero X_* credentials, and leaves x_usage.json untouched.
- OAuth 1.0a authentication via X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
- Tweet posting and sequential thread chaining with in_reply_to_tweet_id
- Budget guard in data/x_usage.json:
  * Resets count each month
  * Hard cap X_MONTHLY_CAP (default: 85)
  * If count >= cap: skip X entirely
  * If count + len(x_posts) > cap: post only x_posts[0]
  * Never retry a create_tweet that may have succeeded (avoids duplicate tweets)
- Dry-run simulation mode
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tweepy
from dotenv import load_dotenv

load_dotenv()

USAGE_FILE = Path(__file__).parent.parent / "data" / "x_usage.json"
DEFAULT_MONTHLY_CAP = 85


def load_x_usage(path: Path | None = None) -> dict[str, Any]:
    """Load monthly X usage count, automatically resetting on month change."""
    file_path = path or USAGE_FILE
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    if not file_path.exists():
        return {"month": current_month, "count": 0}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"month": current_month, "count": 0}
            if data.get("month") != current_month:
                print(f"[post_x] New month detected ({data.get('month')} -> {current_month}). Resetting usage count to 0.")
                return {"month": current_month, "count": 0}
            return data
    except (json.JSONDecodeError, OSError):
        return {"month": current_month, "count": 0}


def save_x_usage(usage: dict[str, Any], path: Path | None = None) -> None:
    """Save monthly X usage count to disk."""
    file_path = path or USAGE_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(usage, f, indent=2)


def get_x_credentials() -> dict[str, str]:
    """Load OAuth 1.0a credentials from environment."""
    api_key = (os.getenv("X_API_KEY") or os.getenv("TWITTER_API_KEY", "")).strip()
    api_secret = (os.getenv("X_API_SECRET") or os.getenv("TWITTER_API_SECRET", "")).strip()
    access_token = (os.getenv("X_ACCESS_TOKEN") or os.getenv("TWITTER_ACCESS_TOKEN", "")).strip()
    access_secret = (
        os.getenv("X_ACCESS_SECRET")
        or os.getenv("X_ACCESS_TOKEN_SECRET")
        or os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
    ).strip()

    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "access_token": access_token,
        "access_secret": access_secret,
    }


def post_to_x(
    posts: list[str],
    dry_run: bool = False,
    usage_file: Path | None = None,
    enable_x: bool | None = None,
) -> list[str]:
    """Publish a single post or sequential thread to X using tweepy.Client.

    Guard:
    - If ENABLE_X is false (default): skips X entirely, touches zero usage files, and requires no credentials.

    Budget guard:
    - If count >= X_MONTHLY_CAP: skip X entirely.
    - If count + len(posts) > X_MONTHLY_CAP: post only posts[0].
    - Never retries a create_tweet call to prevent duplicate posts.

    Returns the list of created Tweet IDs.
    """
    # 0. Check ENABLE_X guard
    is_enabled = enable_x if enable_x is not None else (os.getenv("ENABLE_X", "false").lower() == "true")
    if not is_enabled:
        print("[post_x] ENABLE_X is false. Skipping X entirely (no credentials checked, x_usage.json untouched).")
        return []

    if not posts:
        return []

    is_dry_run = dry_run or os.getenv("DRY_RUN", "").lower() == "true"
    monthly_cap = int(os.getenv("X_MONTHLY_CAP", str(DEFAULT_MONTHLY_CAP)))
    usage_path = usage_file or USAGE_FILE

    # 1. Budget guard check
    usage = load_x_usage(usage_path)
    current_count = int(usage.get("count", 0))

    if current_count >= monthly_cap:
        print(
            f"[post_x] Monthly cap reached ({current_count}/{monthly_cap}). Skipping X posting entirely."
        )
        return []

    posts_to_publish = list(posts)
    if current_count + len(posts_to_publish) > monthly_cap:
        print(
            f"[post_x] Thread of {len(posts_to_publish)} posts would exceed cap "
            f"({current_count} + {len(posts_to_publish)} > {monthly_cap}). Budget guard active: posting only posts[0]."
        )
        posts_to_publish = [posts_to_publish[0]]

    creds = get_x_credentials()
    has_creds = all(bool(v) for v in creds.values())

    if not has_creds and not is_dry_run:
        raise ValueError("Missing X credentials (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET).")

    # 2. Dry run handling
    if is_dry_run or not has_creds:
        print(f"[post_x] DRY RUN: Would publish {len(posts_to_publish)} post(s) to X (Budget: {current_count}/{monthly_cap}):")
        mock_ids: list[str] = []
        for i, post in enumerate(posts_to_publish, start=1):
            mock_id = f"mock_x_{int(datetime.now(timezone.utc).timestamp())}_{i}"
            mock_ids.append(mock_id)
            print(f"  [{i}/{len(posts_to_publish)}] (ID: {mock_id}):\n    {post.replace(chr(10), chr(10) + '    ')}")

        usage["count"] = current_count + len(mock_ids)
        save_x_usage(usage, usage_path)
        return mock_ids

    # 3. Live posting with tweepy.Client
    client = tweepy.Client(
        consumer_key=creds["api_key"],
        consumer_secret=creds["api_secret"],
        access_token=creds["access_token"],
        access_token_secret=creds["access_secret"],
    )

    created_ids: list[str] = []
    previous_tweet_id: str | None = None

    for i, post in enumerate(posts_to_publish, start=1):
        print(f"[post_x] [{i}/{len(posts_to_publish)}] Creating tweet...")
        try:
            # Never retry: execute exactly once to prevent duplicate tweets on network timeout
            if previous_tweet_id:
                response = client.create_tweet(
                    text=post,
                    in_reply_to_tweet_id=previous_tweet_id,
                )
            else:
                response = client.create_tweet(text=post)

            tweet_data = getattr(response, "data", None)
            if not tweet_data or "id" not in tweet_data:
                raise RuntimeError(f"Unexpected response from X API: {response}")

            tweet_id = str(tweet_data["id"])
            created_ids.append(tweet_id)
            previous_tweet_id = tweet_id
            print(f"[post_x] [{i}/{len(posts_to_publish)}] Tweet published successfully -> ID: {tweet_id}")

        except Exception as e:
            # DO NOT RETRY: The tweet might have actually been published on X's backend
            print(
                f"[post_x] Error publishing tweet {i}/{len(posts_to_publish)}: {e}. "
                f"Aborting thread to avoid duplicate tweets.",
                file=sys.stderr,
            )
            # Update usage for what succeeded before failing
            if created_ids:
                usage["count"] = current_count + len(created_ids)
                save_x_usage(usage, usage_path)
            raise

    # 4. Update usage on complete success
    usage["count"] = current_count + len(created_ids)
    save_x_usage(usage, usage_path)
    print(f"[post_x] Updated X monthly usage: {usage['count']}/{monthly_cap}")
    return created_ids
