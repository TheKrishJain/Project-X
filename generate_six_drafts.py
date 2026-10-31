"""Generate 4 morning + 2 night drafts with full grounding, claim-to-evidence tables, and quality verification.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from src.generate import generate_content

REPO_ROOT = Path(__file__).resolve().parent

def main():
    print("=" * 64)
    print("Generating 4 Morning + 2 Night Grounded Drafts")
    print("=" * 64)

    slots_to_generate = [
        {"slot": "morning", "date": "monday", "pillar": "tech_for_business"},
        {"slot": "morning", "date": "tuesday", "pillar": "marketing_case_study"},
        {"slot": "morning", "date": "wednesday", "pillar": "business_strategy"},
        {"slot": "morning", "date": "thursday", "pillar": "ai_hack"},
        {"slot": "night", "date": "monday", "pillar": "psychology_insight"},
        {"slot": "night", "date": "tuesday", "pillar": "motivation"},
    ]

    batch_entities: set[str] = set()
    drafts_data = []

    for idx, cfg in enumerate(slots_to_generate, start=1):
        slot = cfg["slot"]
        pillar = cfg["pillar"]
        date_str = cfg["date"]

        print(f"\n[{idx}/6] Generating {slot.upper()} draft for {date_str} (Pillar: {pillar})...")
        t0 = time.perf_counter()

        draft = generate_content(
            slot=slot,
            date=date_str,
            force_pillar=pillar,
            dry_run=True,
            batch_entities=batch_entities,
        )
        elapsed = time.perf_counter() - t0

        batch_entities.add(draft.entity.lower())

        draft_record = {
            "draft_num": idx,
            "slot": slot.upper(),
            "pillar": draft.pillar,
            "entity": draft.entity,
            "topic": draft.topic,
            "hook_pattern": draft.hook_pattern,
            "format": draft.format,
            "topic_tag": draft.topic_tag,
            "is_trend_jack": draft.is_trend_jack,
            "critique_score": draft.critique_score,
            "critique_reason": draft.critique_reason,
            "critique_details": draft.critique_details,
            "runtime_seconds": draft.runtime_seconds,
            "grounding_sources": draft.grounding_sources,
            "threads_posts": draft.threads_posts,
            "claim_evidence_table": draft.claim_evidence_table,
        }
        drafts_data.append(draft_record)

        print(f"  Entity:       {draft.entity}")
        print(f"  Topic:        {draft.topic}")
        print(f"  Score:        {draft.critique_score}/10")
        print(f"  Runtime:      {draft.runtime_seconds}s")
        print(f"  Sources:      {len(draft.grounding_sources)}")
        print(f"  Claim pairs:  {len(draft.claim_evidence_table)}")

    output_path = REPO_ROOT / "data" / "verified_six_drafts.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(drafts_data, f, indent=2, ensure_ascii=False)

    print(f"\nAll 6 drafts generated and saved to {output_path}")

if __name__ == "__main__":
    main()
