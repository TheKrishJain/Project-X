"""Meta Threads Graph API integration for auto-poster.

Features:
- Official Graph API endpoint: graph.threads.net/v1.0
- Two-step container publishing: POST /{THREADS_USER_ID}/threads -> 5s sleep -> POST /{user_id}/threads_publish
- Sequential reply chaining to previous published post ID
- Topic tag support (passed to root post)
- Strict DASH BAN enforcement (hard-fails if — or – survives)
- refresh_token() support using GET /refresh_access_token?grant_type=th_refresh_token
- Dry-run simulation mode
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv

from src.sanitize import assert_no_dashes, sanitize_post

load_dotenv()

THREADS_BASE_URL = "https://graph.threads.net/v1.0"
THREADS_REFRESH_URL = "https://graph.threads.net/refresh_access_token"


def get_threads_credentials() -> dict[str, str]:
    return {
        "access_token": os.getenv("THREADS_ACCESS_TOKEN", "").strip(),
        "user_id": os.getenv("THREADS_USER_ID", "").strip(),
    }


def refresh_token(access_token: str | None = None) -> dict[str, Any]:
    """Refresh a long-lived Threads User Access Token."""
    token = (access_token or os.getenv("THREADS_ACCESS_TOKEN", "")).strip()
    if not token:
        raise ValueError("Cannot refresh Threads token: access_token is empty.")

    params = {
        "grant_type": "th_refresh_token",
        "access_token": token,
    }

    print("[post_threads] Requesting Threads token refresh...")
    response = requests.get(THREADS_REFRESH_URL, params=params, timeout=20)
    if response.status_code != 200:
        err_msg = f"Failed to refresh Threads token: {response.status_code} {response.text}"
        print(f"[post_threads] {err_msg}", file=sys.stderr)
        response.raise_for_status()

    data: dict[str, Any] = response.json()
    expires_in = data.get("expires_in")
    print(f"[post_threads] Successfully refreshed token! Valid for ~{int(expires_in or 0) // 86400} days.")
    return data


def get_post_details(post_id: str, access_token: str | None = None) -> dict[str, Any]:
    """Retrieve details (id, text, permalink, timestamp) of a published Threads post."""
    token = (access_token or os.getenv("THREADS_ACCESS_TOKEN", "")).strip()
    if not token or not post_id:
        return {}
    url = f"{THREADS_BASE_URL}/{post_id}"
    params = {
        "fields": "id,text,permalink,timestamp",
        "access_token": token,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[post_threads] Notice: could not fetch post details for {post_id}: {e}")
    return {}


def post_to_threads(
    posts: list[str],
    dry_run: bool = False,
    topic_tag: str | None = None,
) -> list[str]:
    """Publish single post or sequential thread to Meta Threads.

    For each post:
    1. Sanitize text and verify DASH BAN (hard fail on em/en dash)
    2. POST /{THREADS_USER_ID}/threads with media_type=TEXT, text, reply_to_id (for 2nd+ items), topic_tag (for 1st item)
    3. Sleep 5 seconds
    4. POST /{THREADS_USER_ID}/threads_publish with creation_id
    5. Chain replies to the previous published ID

    Returns the list of published Threads post IDs.
    """
    if not posts:
        return []

    # Apply strict sanitization and dash ban before anything else
    sanitized_posts: list[str] = []
    for post in posts:
        clean = sanitize_post(post)
        assert_no_dashes(clean)
        sanitized_posts.append(clean)

    is_dry_run = dry_run or os.getenv("DRY_RUN", "").lower() == "true"
    creds = get_threads_credentials()
    access_token = creds["access_token"]
    user_id = creds["user_id"]
    has_creds = bool(access_token and user_id)

    if not has_creds and not is_dry_run:
        raise ValueError("Missing Threads credentials (THREADS_ACCESS_TOKEN, THREADS_USER_ID).")

    if is_dry_run or not has_creds:
        print(f"[post_threads] DRY RUN: Would publish {len(sanitized_posts)} post(s) to Threads (topic_tag: {topic_tag or 'None'}):")
        mock_ids: list[str] = []
        for i, post in enumerate(sanitized_posts, start=1):
            mock_id = f"mock_th_{int(datetime.now(timezone.utc).timestamp())}_{i}"
            mock_ids.append(mock_id)
            print(f"  [{i}/{len(sanitized_posts)}] (ID: {mock_id}):\n    {post.replace(chr(10), chr(10) + '    ')}")
        return mock_ids

    published_ids: list[str] = []
    previous_published_id: str | None = None

    for i, post in enumerate(sanitized_posts, start=1):
        create_url = f"{THREADS_BASE_URL}/{user_id}/threads"
        create_payload: dict[str, Any] = {
            "media_type": "TEXT",
            "text": post,
            "access_token": access_token,
        }
        if previous_published_id:
            create_payload["reply_to_id"] = previous_published_id
        if i == 1 and topic_tag:
            create_payload["topic_tag"] = topic_tag

        print(f"[post_threads] [{i}/{len(sanitized_posts)}] Creating media container...")
        create_resp = requests.post(create_url, data=create_payload, timeout=25)

        # Graceful retry if topic_tag parameter is rejected by endpoint
        if create_resp.status_code not in (200, 201) and "topic_tag" in create_payload:
            print("[post_threads] Notice: topic_tag not accepted by endpoint, retrying container without topic_tag...")
            create_payload.pop("topic_tag", None)
            create_resp = requests.post(create_url, data=create_payload, timeout=25)

        if create_resp.status_code not in (200, 201):
            print(
                f"[post_threads] Container creation failed on post {i}: {create_resp.status_code} {create_resp.text}",
                file=sys.stderr,
            )
            create_resp.raise_for_status()

        creation_id = str(create_resp.json().get("id"))
        print(f"[post_threads] [{i}/{len(sanitized_posts)}] Container created (ID: {creation_id}). Waiting 5s...")

        # Wait 5s before publishing
        time.sleep(5)

        # Step 2: Publish media container
        publish_url = f"{THREADS_BASE_URL}/{user_id}/threads_publish"
        publish_payload = {
            "creation_id": creation_id,
            "access_token": access_token,
        }

        publish_resp = requests.post(publish_url, data=publish_payload, timeout=25)
        if publish_resp.status_code not in (200, 201):
            print(
                f"[post_threads] Publishing container {creation_id} failed: {publish_resp.status_code} {publish_resp.text}",
                file=sys.stderr,
            )
            publish_resp.raise_for_status()

        published_post_id = str(publish_resp.json().get("id"))
        published_ids.append(published_post_id)
        previous_published_id = published_post_id
        print(f"[post_threads] [{i}/{len(sanitized_posts)}] Published successfully -> Post ID: {published_post_id}")

    return published_ids
