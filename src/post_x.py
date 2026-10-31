"""X (Twitter) API v2 posting integration.

Handles OAuth 1.0a authentication, monthly rate-limit tracking via data/x_usage.json,
and single-post or thread publishing.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from requests_oauthlib import OAuth1

load_dotenv()

USAGE_FILE = Path(__file__).parent.parent / "data" / "x_usage.json"
X_TWEETS_ENDPOINT = "https://api.twitter.com/2/tweets"


def load_x_usage(path: Path = USAGE_FILE) -> dict[str, Any]:
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    if not path.exists():
        return {"month": current_month, "count": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"month": current_month, "count": 0}
            if data.get("month") != current_month:
                return {"month": current_month, "count": 0}
            return data
    except (json.JSONDecodeError, OSError):
        return {"month": current_month, "count": 0}


def save_x_usage(usage: dict[str, Any], path: Path = USAGE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(usage, f, indent=2)


def get_x_credentials() -> dict[str, str]:
    api_key = os.getenv("X_API_KEY") or os.getenv("TWITTER_API_KEY", "")
    api_secret = os.getenv("X_API_SECRET") or os.getenv("TWITTER_API_SECRET", "")
    access_token = os.getenv("X_ACCESS_TOKEN") or os.getenv("TWITTER_ACCESS_TOKEN", "")
    access_secret = os.getenv("X_ACCESS_TOKEN_SECRET") or os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "access_token": access_token,
        "access_secret": access_secret,
    }


def post_to_x(posts: list[str], dry_run: bool = False) -> list[str]:
    """Publish a single post or a sequential thread to X.

    Returns the list of created Tweet IDs.
    """
    if not posts:
        return []

    is_dry_run = dry_run or os.getenv("DRY_RUN", "").lower() == "true"
    creds = get_x_credentials()
    has_creds = all(bool(v) for v in creds.values())

    if not has_creds and not is_dry_run:
        raise ValueError("Missing X credentials (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET).")

    # Check and update usage quota
    usage = load_x_usage()
    monthly_limit = int(os.getenv("X_MONTHLY_LIMIT", "500"))
    if usage["count"] + len(posts) > monthly_limit:
        msg = f"X monthly tweet limit exceeded: {usage['count']}/{monthly_limit} used."
        if not is_dry_run:
            raise RuntimeError(msg)
        print(f"[post_x] Warning: {msg}")

    if is_dry_run or not has_creds:
        print(f"[post_x] DRY RUN: Would publish {len(posts)} post(s) to X:")
        mock_ids: list[str] = []
        for i, post in enumerate(posts, start=1):
            mock_id = f"mock_x_{int(datetime.now(timezone.utc).timestamp())}_{i}"
            mock_ids.append(mock_id)
            print(f"  [{i}/{len(posts)}] (ID: {mock_id}):\n    {post.replace(chr(10), chr(10) + '    ')}")

        # Update local usage counter in dry run as well
        usage["count"] += len(posts)
        save_x_usage(usage)
        return mock_ids

    # Live posting via X API v2
    auth = OAuth1(
        client_key=creds["api_key"],
        client_secret=creds["api_secret"],
        resource_owner_key=creds["access_token"],
        resource_owner_secret=creds["access_secret"],
    )

    created_ids: list[str] = []
    previous_tweet_id: str | None = None

    for i, post in enumerate(posts, start=1):
        payload: dict[str, Any] = {"text": post}
        if previous_tweet_id:
            payload["reply"] = {"in_reply_to_tweet_id": previous_tweet_id}

        response = requests.post(
            X_TWEETS_ENDPOINT,
            json=payload,
            auth=auth,
            headers={"Content-Type": "application/json"},
            timeout=20,
        )

        if response.status_code not in (200, 201):
            print(f"[post_x] Error posting tweet {i}/{len(posts)}: {response.status_code} {response.text}", file=sys.stderr)
            response.raise_for_status()

        data = response.json().get("data", {})
        tweet_id = str(data.get("id"))
        created_ids.append(tweet_id)
        previous_tweet_id = tweet_id
        print(f"[post_x] Posted tweet {i}/{len(posts)} -> ID: {tweet_id}")

    # Persist usage
    usage["count"] += len(created_ids)
    save_x_usage(usage)
    return created_ids
