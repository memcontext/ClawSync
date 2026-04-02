#!/usr/bin/env python3
"""
score_meeting API
Scores all user time data for a specified meeting, outputs to meeting_score/{meeting_id}.json.

Time slot key format: YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM
Dynamically collects all time slots mentioned by users, not dependent on a fixed list.

Scoring rules:
  - score   : Number of users with value True (available) in that time slot
  - conflict: List of user IDs with value False (explicitly unavailable) in that time slot
  - User did not mention the time slot -> ignored (not counted in score or conflict)

Public interface:
    score_meeting(meeting_id: str) -> dict
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field, RootModel

from .agent_input_format import _load_store
from .logger import get_logger

logger = get_logger("scoring")

# ─── Configuration ────────────────────────────────────────────────────────────

SCORE_DIR = Path(__file__).resolve().parent.parent / "meeting_score"


def _score_file(meeting_id: str) -> Path:
    SCORE_DIR.mkdir(parents=True, exist_ok=True)
    return SCORE_DIR / f"{meeting_id}.json"

# ─── Pydantic Models ─────────────────────────────────────────────────────────

class SlotScore(BaseModel):
    score: int = Field(description="Number of users available in this time slot (True count)")
    conflict: list[str] = Field(description="List of user IDs explicitly unavailable in this time slot (False users)")


class MeetingScore(RootModel[dict[str, SlotScore]]):
    """
    Complete meeting scoring result.
    Key is a date-aware time slot (YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM), value is SlotScore.
    """

# ─── Public API ───────────────────────────────────────────────────────────────

def score_meeting(meeting_id: str) -> dict:
    """
    Score time slots for the specified meeting and save the results.

    Dynamically collects all time slot keys mentioned by users, tallies per slot:
      - True  -> score +1
      - False -> add to conflict list
      - Not present (user did not mention) -> ignore

    Returns:
        {
            "2026-03-21 10:00--2026-03-21 10:30": {"score": 2, "conflict": []},
            "2026-03-21 10:30--2026-03-21 11:00": {"score": 1, "conflict": ["2"]},
            ...
        }
    """
    from .agent_input_format import DATA_DIR
    if not (DATA_DIR / f"{meeting_id}.json").exists():
        raise FileNotFoundError(
            f"Meeting data file not found: {DATA_DIR / f'{meeting_id}.json'}"
        )

    store = _load_store(meeting_id)
    logger.info("Scoring started: meeting=%s, participants=%d", meeting_id, len(store.root))

    # ── Step 1: Dynamically collect all time slot keys mentioned by users ──
    all_slots: set[str] = set()
    for user_id, entry in store.root.items():
        for key in (entry.model_extra or {}):
            # Only collect slot keys (containing "--"), exclude user_ID / meeting_ID and other metadata
            if "--" in key:
                all_slots.add(key)

    # Sort by time (string sorting works since the format is uniform)
    sorted_slots = sorted(all_slots)

    # ── Step 2: Score each slot ────────────────────────────────────────────
    # For standard format users (only True slots stored), absent slots are treated as unavailable (conflict)
    result: dict[str, SlotScore] = {}
    for slot in sorted_slots:
        score = 0
        conflict: list[str] = []

        for user_id, entry in store.root.items():
            extras = entry.model_extra or {}
            val = extras.get(slot)
            if val is True:
                score += 1
            elif val is False:
                conflict.append(user_id)
            else:
                # User did not mention this slot: if the user has other slot data, it's standard format,
                # absent slot = unavailable -> count as conflict
                has_any_slot = any("--" in k for k in extras)
                if has_any_slot:
                    conflict.append(user_id)
                # Otherwise the user has no data at all, ignore

        result[slot] = SlotScore(score=score, conflict=conflict)

    meeting_score = MeetingScore.model_validate(result)

    # Statistics summary
    total_slots = len(result)
    slots_with_conflict = sum(1 for s in result.values() if s.conflict)
    max_score = max((s.score for s in result.values()), default=0)
    logger.info("Scoring completed: meeting=%s, total_slots=%d, conflicting_slots=%d, max_score=%d",
                meeting_id, total_slots, slots_with_conflict, max_score)
    for slot_key, slot_score in result.items():
        if slot_score.conflict:
            logger.debug("  Conflicting slot: %s -> score=%d, conflict=%s", slot_key, slot_score.score, slot_score.conflict)

    path = _score_file(meeting_id)
    path.write_text(
        json.dumps(meeting_score.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return meeting_score.model_dump()
