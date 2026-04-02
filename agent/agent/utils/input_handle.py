#!/usr/bin/env python3
"""
handle_meeting / coordinate_meeting / coordinate_from_task API

Public interface:
    handle_meeting(role_inputs, meeting_id) -> dict          # Collect + score
    coordinate_meeting(role_inputs, meeting_id) -> dict      # Collect + score + LLM recommendation
    coordinate_from_task(task) -> dict                       # Directly accepts API 7 format
"""

import json

from .agent_input_format import submit_user_time
from .output_summary import summarize_meeting
from .scoring import score_meeting
from .logger import get_logger

logger = get_logger("input_handle")

# ─── Public API ───────────────────────────────────────────────────────────────

def handle_meeting(
    role_inputs: list[tuple[str, "str | list[dict]"]],
    meeting_id: str,
    reference_date: str | None = None,
) -> dict:
    """
    Collect time descriptions from all meeting participants, format and store them, then score.

    Args:
        role_inputs : Participant list, each item is a (user_id, user_input) tuple.
                      user_id    -- User unique identifier
                      user_input -- Time description, supports two formats:
                          - Natural language string: e.g., "I'm free tonight from 6 to 7:30"
                          - Standard API format: e.g., [{"start": "2026-03-18 14:00",
                                                         "end":   "2026-03-18 16:00"}, ...]
        meeting_id  : Meeting unique identifier

    Returns:
        Meeting scoring result dict, format:
        {
            "18:00-18:30": {"score": 2, "conflict": ["user_003"]},
            "18:30-19:00": {"score": 3, "conflict": []},
            ...
        }

    Example:
        result = handle_meeting(
            role_inputs=[
                ("user_001", "I'm free tonight from 6 to 7:30"),
                ("user_002", [{"start": "2026-03-18 18:30", "end": "2026-03-18 19:00"}]),
                ("user_003", "Free all evening"),
            ],
            meeting_id="meeting_12345",
        )
    """
    # ── Phase 1: Format time descriptions for each user ────────────────────
    logger.info("handle_meeting started: meeting=%s, participants=%d", meeting_id, len(role_inputs))
    for user_id, user_input in role_inputs:
        if isinstance(user_input, list):
            logger.info("[%s] Standard format, parsing directly (%d time slots)", user_id, len(user_input))
        else:
            logger.info("[%s] Natural language, calling Agent to parse: '%s'", user_id, user_input)
        submit_user_time(
            user_input=user_input,
            user_id=user_id,
            meeting_id=meeting_id,
            reference_date=reference_date,
        )

    # ── Phase 2: Aggregate scoring ─────────────────────────────────────────
    logger.info("Scoring %s...", meeting_id)
    score = score_meeting(meeting_id)

    return score


def coordinate_meeting(
    role_inputs: list[tuple[str, "str | list[dict]"]],
    meeting_id: str,
    reference_date: str | None = None,
) -> dict:
    """
    One-stop meeting time coordination: collect user times -> score -> LLM recommendation.

    Args:
        role_inputs : Participant list, each item is a (user_id, user_input) tuple.
                      user_input supports natural language string or standard API format list[dict].
        meeting_id  : Meeting unique identifier

    Returns:
        coordinator_result dict, one of two formats:

        Common free time found (CONFIRMED):
        {
            "status": "CONFIRMED",
            "final_time": "2026-01-01 18:00-2026-01-01 18:30",
            "reasoning": "2 people are available in this time slot with no conflicts",
            "alternative_slots": ["2026-01-01 18:30-2026-01-01 19:00"]
        }

        No common free time (NEGOTIATING):
        {
            "status": "NEGOTIATING",
            "reasoning": "All time slots have conflicts",
            "suggestions": ["Suggest expanding available time range"]
        }
    """
    handle_meeting(role_inputs=role_inputs, meeting_id=meeting_id, reference_date=reference_date)
    logger.info("Analyzing recommended time for %s...", meeting_id)
    return summarize_meeting(meeting_id)


def coordinate_from_task(task: dict) -> dict:
    """
    Directly accepts a single task object returned by API 7 (GET /api/agent/tasks/pending),
    executes the full coordination flow, and returns an API 8 format request body.

    Args:
        task: A single task from API 7 pending_tasks, example structure:
            {
                "meeting_id": "mtg_xxx",
                "title": "Project Discussion",
                "participants_data": [
                    {
                        "user_id": 1,
                        "email": "alice@example.com",
                        "role": "initiator",
                        "latest_slots": [
                            {"start": "2026-03-18 14:00", "end": "2026-03-18 16:00"}
                        ],
                        "preference_note": "Try to schedule in the morning"
                    },
                    ...
                ]
            }

    Returns:
        API 8 format dict:
        {
            "decision_status": "CONFIRMED",
            "final_time": "2026-01-01 15:00-15:30",
            "agent_reasoning": "...",
            "counter_proposals": []
        }
    """
    meeting_id: str = task["meeting_id"]
    duration_minutes: int = task.get("duration_minutes", 30)
    round_count: int = task.get("round_count", 0)
    max_rounds: int = task.get("max_rounds", 3)
    previous_reasoning: str | None = task.get("previous_reasoning")
    participants_data: list[dict] = task["participants_data"]

    logger.info("=" * 60)
    logger.info("coordinate_from_task started")
    logger.info("  meeting_id      : %s", meeting_id)
    logger.info("  title           : %s", task.get("title", "N/A"))
    logger.info("  duration_minutes: %d", duration_minutes)
    logger.info("  round_count     : %d / max_rounds: %d", round_count, max_rounds)
    logger.info("  participants    : %d people", len(participants_data))
    for p in participants_data:
        logger.info("    [%s] %s (user_id=%s), slots=%s, note=%r",
                     p.get("role", "?"), p.get("email", "?"), p.get("user_id", "?"),
                     json.dumps(p.get("latest_slots") or [], ensure_ascii=False),
                     p.get("preference_note"))
    if previous_reasoning:
        logger.info("  previous_reasoning: %s", previous_reasoning)
    logger.info("=" * 60)

    # ── FAILED: exceeded maximum negotiation rounds ────────────────────────
    if round_count >= max_rounds:
        logger.warning("%s reached max negotiation rounds (%d/%d), returning FAILED", meeting_id, round_count, max_rounds)
        return {
            "decision_status": "FAILED",
            "final_time": None,
            "agent_reasoning": f"After {max_rounds} rounds of negotiation, participants still could not agree on a meeting time.",
            "counter_proposals": [],
        }

    # ── Validation: must have exactly one initiator ────────────────────────
    initiators = [p for p in participants_data if p.get("role") == "initiator"]
    if len(initiators) == 0:
        return {
            "decision_status": "NEGOTIATING",
            "final_time": None,
            "agent_reasoning": "Error: no initiator found in the meeting, unable to perform time coordination",
            "counter_proposals": [],
        }
    if len(initiators) > 1:
        emails = [p.get("email", str(p["user_id"])) for p in initiators]
        return {
            "decision_status": "NEGOTIATING",
            "final_time": None,
            "agent_reasoning": f"Error: meeting has multiple initiators ({emails}), only one initiator is allowed per meeting",
            "counter_proposals": [],
        }

    initiator_id = str(initiators[0]["user_id"])

    # ── Extract actual date from latest_slots (take the first valid date) ──
    meeting_date: str | None = None
    for p in participants_data:
        for slot in (p.get("latest_slots") or []):
            start_str = str(slot.get("start", ""))
            if " " in start_str:
                meeting_date = start_str.split(" ")[0]  # "2026-03-21"
                break
        if meeting_date:
            break

    # ── Collect time input from each participant ───────────────────────────
    import re
    _SLOT_FMT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")  # YYYY-MM-DD HH:MM

    role_inputs: list[tuple[str, "str | list[dict]"]] = []
    for p in participants_data:
        user_id = str(p["user_id"])
        email = p.get("email", user_id)
        slots: list[dict] = p.get("latest_slots") or []
        note: str = (p.get("preference_note") or "").strip()

        if slots:
            # Validate latest_slots format
            valid = True
            for i, slot in enumerate(slots):
                if not isinstance(slot, dict):
                    logger.error("[%s] latest_slots[%d] is not a dict: %s", email, i, slot)
                    valid = False
                    continue
                start_val = slot.get("start")
                end_val = slot.get("end")
                if start_val is None or end_val is None:
                    logger.error("[%s] latest_slots[%d] missing start/end fields: %s", email, i, slot)
                    valid = False
                elif not _SLOT_FMT.match(str(start_val)) or not _SLOT_FMT.match(str(end_val)):
                    logger.error("[%s] latest_slots[%d] format error, expected 'YYYY-MM-DD HH:MM', actual start=%r, end=%r",
                                email, i, start_val, end_val)
                    valid = False

            if valid:
                role_inputs.append((user_id, slots))
            else:
                logger.warning("[%s] latest_slots validation failed, skipping this participant", email)
        elif note:
            role_inputs.append((user_id, note))
        else:
            logger.warning("[%s] No time data (both latest_slots and preference_note are empty), skipping", email)

    handle_meeting(role_inputs=role_inputs, meeting_id=meeting_id, reference_date=meeting_date)
    logger.info("Analyzing recommended time for %s (duration %d min, round %d/%d, prioritizing initiator alignment)...",
                meeting_id, duration_minutes, round_count, max_rounds)
    result = summarize_meeting(
        meeting_id=meeting_id,
        duration_minutes=duration_minutes,
        initiator_id=initiator_id,
        participants_info=participants_data,
        meeting_date=meeting_date,
        round_count=round_count,
        max_rounds=max_rounds,
        previous_reasoning=previous_reasoning,
    )
    logger.info("coordinate_from_task completed: meeting=%s, decision=%s", meeting_id, result.get("decision_status"))
    logger.info("Output result: %s", json.dumps(result, ensure_ascii=False, indent=2))
    return result
