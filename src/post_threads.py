"""Meta Threads Graph API integration.

Handles container creation and publishing for single posts and sequential threads.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

THREADS_BASE_URL = "https://graph.threads.net/v1.0"


def get_threads_credentials() -> dict[str, str]:
    return {
        "access_token": os.getenv("THREADS_ACCESS_TOKEN", "").strip(),
        "user_id": os.getenv("THREADS_USER_ID", "").strip(),
    }


def _wait_for_container(creation_id: str, access_token: str, max_retries: int = 5, delay: float = 2.0) -> None:
    """Optionally poll container status before publishing."""
    url = f"{THREADS_BASE_URL}/{creation_id}"
    params = {"fields": "status_code,error_message", "access_token": access_token}

    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status_code", "").upper()
                if status == "FINISHED":
                    return
                if status == "ERROR":
                    err = data.get("error_message", "Unknown container error")
                    raise RuntimeError(f"Threads container failed: {err}")
        except Exception:
            pass
        time.sleep(delay)


def post_to_threads(posts: list[str], dry_run: bool = False) -> list[str]:
    """Publish a single post or a sequential thread to Meta Threads.

    Returns the list of created Threads Post IDs.
    """
    if not posts:
        return []

    is_dry_run = dry_run or os.getenv("DRY_RUN", "").lower() == "true"
    creds = get_threads_credentials()
    access_token = creds["access_token"]
    user_id = creds["user_id"]
    has_creds = bool(access_token and user_id)

    if not has_creds and not is_dry_run:
        raise ValueError("Missing Threads credentials (THREADS_ACCESS_TOKEN, THREADS_USER_ID).")

    if is_dry_run or not has_creds:
        print(f"[post_threads] DRY RUN: Would publish {len(posts)} post(s) to Threads:")
        mock_ids: list[str] = []
        for i, post in enumerate(posts, start=1):
            mock_id = f"mock_th_{int(datetime.now(timezone.utc).timestamp())}_{i}"
            mock_ids.append(mock_id)
            print(f"  [{i}/{len(posts)}] (ID: {mock_id}):\n    {post.replace(chr(10), chr(10) + '    ')}")
        return mock_ids

    created_post_ids: list[str] = []
    previous_post_id: str | None = None

    for i, post in enumerate(posts, start=1):
        # 1. Create Media Container
        create_url = f"{THREADS_BASE_URL}/{user_id}/threads"
        create_payload: dict[str, Any] = {
            "media_type": "TEXT",
            "text": post,
            "access_token": access_token,
        }
        if previous_post_id:
            create_payload["reply_to_id"] = previous_post_id

        create_resp = requests.post(create_url, data=create_payload, timeout=20)
        if create_resp.status_code not in (200, 201):
            print(
                f"[post_threads] Error creating container for post {i}: {create_resp.status_code} {create_resp.text}",
                file=sys.stderr,
            )
            create_resp.raise_for_status()

        creation_id = str(create_resp.json().get("id"))

        # Wait briefly for container processing
        _wait_for_container(creation_id, access_token)

        # 2. Publish Media Container
        publish_url = f"{THREADS_BASE_URL}/{user_id}/threads_publish"
        publish_payload = {
            "creation_id": creation_id,
            "access_token": access_token,
        }

        publish_resp = requests.post(publish_url, data=publish_payload, timeout=20)
        if publish_resp.status_code not in (200, 201):
            print(
                f"[post_threads] Error publishing container {creation_id}: {publish_resp.status_code} {publish_resp.text}",
                file=sys.stderr,
            )
            publish_resp.raise_for_status()

        post_id = str(publish_resp.json().get("id"))
        created_post_ids.append(post_id)
        previous_post_id = post_id
        print(f"[post_threads] Published post {i}/{len(posts)} -> ID: {post_id}")

        # Minor safety pause between thread replies
        if i < len(posts):
            time.sleep(2)

    return created_post_ids
