"""Analytics feedback loop and weight optimization engine for Project-X.

Features:
- Daily: fetches insights (views, likes, replies, reposts, quotes) for posts > 24h old via Threads API
- Calculates engagement score: views + 5*likes + 15*replies + 20*reposts + 20*quotes
- Stores performance history in data/performance.json
- Weekly (Sunday): recomputes dynamic weights in data/weights.json
  (top 30% boosted up to 2x, bottom 30% reduced to 0.5x, 20% exploration preserved)
- Exports top 5 performing posts as audience voice reference
- Writes weekly performance report to reports/week-YYYY-WW.md
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
REPORTS_DIR = Path(__file__).parent.parent / "reports"
PERFORMANCE_FILE = DATA_DIR / "performance.json"
WEIGHTS_FILE = DATA_DIR / "weights.json"
HISTORY_FILE = DATA_DIR / "history.json"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def calculate_score(views: int, likes: int, replies: int, reposts: int, quotes: int) -> int:
    """Engagement Score = views + 5*likes + 15*replies + 20*reposts + 20*quotes"""
    return int(views + (5 * likes) + (15 * replies) + (20 * reposts) + (20 * quotes))


def fetch_threads_insights(post_id: str, access_token: str) -> dict[str, int]:
    """Fetch engagement metrics for a published Threads media object."""
    metrics = {"views": 0, "likes": 0, "replies": 0, "reposts": 0, "quotes": 0}
    if not post_id or post_id.startswith("mock_"):
        return metrics

    url = f"https://graph.threads.net/v1.0/{post_id}/insights"
    params = {
        "metric": "views,likes,replies,reposts,quotes",
        "access_token": access_token,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for item in data:
                name = item.get("name")
                values = item.get("values", [{}])
                val = values[0].get("value", 0) if values else 0
                if name in metrics:
                    metrics[name] = int(val)
        else:
            print(f"[analytics] Notice: insights for {post_id} returned HTTP {resp.status_code}")
    except Exception as e:
        print(f"[analytics] Error fetching insights for post {post_id}: {e}")

    return metrics


def sync_performance_metrics() -> list[dict[str, Any]]:
    """Scan history.json, fetch metrics for posts > 24h old, and update performance.json."""
    token = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
    history = load_json(HISTORY_FILE, [])
    performance = load_json(PERFORMANCE_FILE, [])
    perf_by_id = {item.get("post_id"): item for item in performance if item.get("post_id")}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    updated = False

    for entry in history:
        threads_ids = entry.get("threads_post_ids") or []
        if not threads_ids:
            continue
        first_post_id = threads_ids[0]
        if first_post_id.startswith("mock_"):
            continue

        timestamp_str = entry.get("timestamp")
        try:
            post_time = datetime.fromisoformat(timestamp_str) if timestamp_str else now
        except ValueError:
            post_time = now

        # Only process posts older than 24 hours
        if post_time > cutoff:
            continue

        existing = perf_by_id.get(first_post_id)
        # Fetch or refresh insights
        insights = fetch_threads_insights(first_post_id, token) if token else {}
        views = insights.get("views", 0)
        likes = insights.get("likes", 0)
        replies = insights.get("replies", 0)
        reposts = insights.get("reposts", 0)
        quotes = insights.get("quotes", 0)

        # If already recorded with existing non-zero metrics, don't overwrite with zeros
        if existing and sum(insights.values()) == 0:
            views = existing.get("views", 0)
            likes = existing.get("likes", 0)
            replies = existing.get("replies", 0)
            reposts = existing.get("reposts", 0)
            quotes = existing.get("quotes", 0)

        score = calculate_score(views, likes, replies, reposts, quotes)
        th_posts = entry.get("threads_posts", [])
        first_text = th_posts[0] if th_posts else ""

        perf_entry = {
            "post_id": first_post_id,
            "timestamp": timestamp_str,
            "topic": entry.get("topic", "Untitled"),
            "pillar": entry.get("pillar", "business_strategy"),
            "hook": entry.get("hook_pattern", "counterintuitive_claim"),
            "format": entry.get("format", "thread" if entry.get("is_threads_thread") else "short_insight"),
            "topic_tag": entry.get("topic_tag", "Business"),
            "slot": entry.get("slot", "morning"),
            "length": len(first_text),
            "views": views,
            "likes": likes,
            "replies": replies,
            "reposts": reposts,
            "quotes": quotes,
            "score": score,
            "sample_text": first_text[:140],
        }

        perf_by_id[first_post_id] = perf_entry
        updated = True

    if updated or not performance:
        performance_list = list(perf_by_id.values())
        save_json(PERFORMANCE_FILE, performance_list)
        print(f"[analytics] Synchronized {len(performance_list)} post performance records.")
        return performance_list

    return performance


def update_dynamic_weights() -> dict[str, Any]:
    """Recompute dynamic weights for pillars, hooks, and formats based on performance scores."""
    performance = load_json(PERFORMANCE_FILE, [])
    weights = load_json(WEIGHTS_FILE, {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pillars": {},
        "hooks": {},
        "formats": {},
    })

    if not performance:
        print("[analytics] No performance records found yet; keeping baseline weights.")
        return weights

    # Helper to compute category weights: top 30% boosted up to 2x, bottom 30% reduced down to 0.5x
    def _optimize_group(category_key: str, existing_dict: dict[str, float]) -> dict[str, float]:
        scores_by_item: dict[str, list[int]] = {}
        for p in performance:
            val = p.get(category_key)
            if val:
                scores_by_item.setdefault(val, []).append(p.get("score", 0))

        if not scores_by_item:
            return existing_dict

        avg_scores = {k: sum(v) / len(v) for k, v in scores_by_item.items()}
        sorted_items = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)
        n = len(sorted_items)
        top_count = max(1, int(n * 0.30))
        bottom_count = max(1, int(n * 0.30))

        top_set = {k for k, _ in sorted_items[:top_count]}
        bottom_set = {k for k, _ in sorted_items[-bottom_count:]}

        new_weights = dict(existing_dict)
        for k in new_weights.keys():
            if k in top_set:
                new_weights[k] = round(min(2.0, new_weights.get(k, 1.0) * 1.25), 2)
            elif k in bottom_set:
                new_weights[k] = round(max(0.5, new_weights.get(k, 1.0) * 0.80), 2)
            else:
                # Neutral reversion toward 1.0
                current = new_weights.get(k, 1.0)
                new_weights[k] = round(current * 0.95 + 1.0 * 0.05, 2)

        return new_weights

    weights["pillars"] = _optimize_group("pillar", weights.get("pillars", {}))
    weights["hooks"] = _optimize_group("hook", weights.get("hooks", {}))
    weights["formats"] = _optimize_group("format", weights.get("formats", {}))
    weights["updated_at"] = datetime.now(timezone.utc).isoformat()

    save_json(WEIGHTS_FILE, weights)
    print(f"[analytics] Updated dynamic weights saved to {WEIGHTS_FILE}")
    return weights


def get_top_performing_posts(limit: int = 5) -> list[dict[str, Any]]:
    """Return top N performing posts by score of all time (for generator voice reference)."""
    performance = load_json(PERFORMANCE_FILE, [])
    if not performance:
        return []
    sorted_posts = sorted(performance, key=lambda x: x.get("score", 0), reverse=True)
    return sorted_posts[:limit]


def generate_weekly_report() -> Path:
    """Generate weekly markdown performance report in reports/week-YYYY-WW.md."""
    performance = load_json(PERFORMANCE_FILE, [])
    weights = load_json(WEIGHTS_FILE, {})
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    report_filename = f"week-{year}-{week:02d}.md"
    report_path = REPORTS_DIR / report_filename

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    sorted_posts = sorted(performance, key=lambda x: x.get("score", 0), reverse=True)
    top_3 = sorted_posts[:3]
    worst_3 = sorted_posts[-3:] if len(sorted_posts) >= 3 else []

    # Calculate pillar and hook winners
    pillar_scores: dict[str, list[int]] = {}
    hook_scores: dict[str, list[int]] = {}
    for p in performance:
        if p.get("pillar"):
            pillar_scores.setdefault(p["pillar"], []).append(p.get("score", 0))
        if p.get("hook"):
            hook_scores.setdefault(p["hook"], []).append(p.get("score", 0))

    best_pillar = max(pillar_scores.items(), key=lambda x: sum(x[1])/len(x[1]))[0] if pillar_scores else "N/A"
    best_hook = max(hook_scores.items(), key=lambda x: sum(x[1])/len(x[1]))[0] if hook_scores else "N/A"

    lines = [
        f"# Weekly Performance Report: Week {week}, {year}",
        f"\nGenerated: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Total Posts Evaluated: {len(performance)}",
        "\n## Executive Summary",
        f"- **Winning Pillar**: `{best_pillar}`",
        f"- **Winning Hook Pattern**: `{best_hook}`",
        "- **Exploration Baseline**: 20% pure exploratory distribution preserved",
        "\n## Top 3 Performing Posts",
    ]

    if top_3:
        for idx, p in enumerate(top_3, 1):
            lines.append(
                f"{idx}. **{p.get('topic')}** (Score: {p.get('score')} | Views: {p.get('views')} | Likes: {p.get('likes')} | Replies: {p.get('replies')})\n"
                f"   - Pillar: `{p.get('pillar')}` | Hook: `{p.get('hook')}` | Format: `{p.get('format')}`\n"
                f"   - Opening: \"{p.get('sample_text', '')}\""
            )
    else:
        lines.append("*No published post data available yet.*")

    lines.append("\n## Lowest 3 Performing Posts")
    if worst_3:
        for idx, p in enumerate(worst_3, 1):
            lines.append(
                f"{idx}. **{p.get('topic')}** (Score: {p.get('score')} | Views: {p.get('views')})\n"
                f"   - Pillar: `{p.get('pillar')}` | Hook: `{p.get('hook')}` | Format: `{p.get('format')}`"
            )
    else:
        lines.append("*Insufficient posts for low-bracket ranking.*")

    lines.append("\n## Updated Weights Distribution")
    lines.append("```json")
    lines.append(json.dumps(weights, indent=2))
    lines.append("```")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[analytics] Weekly report written to {report_path}")
    return report_path


def run_analytics_cycle() -> None:
    """Run full analytics synchronization, weight update, and report generation."""
    print("=" * 64)
    print("Project-X Analytics & Feedback Loop Execution")
    print("=" * 64)
    sync_performance_metrics()
    update_dynamic_weights()
    generate_weekly_report()
    print("=" * 64)


if __name__ == "__main__":
    run_analytics_cycle()
