#!/usr/bin/env python3
"""
summarize_meeting API
Reads meeting_score/{meeting_id}.json, uses LLM Agent to analyze scoring data,
and returns a request body conforming to API 8 (POST /api/agent/meetings/{meeting_id}/result).

Supports three decision states:
  - CONFIRMED   : Found a time block where all participants are available
  - NEGOTIATING : No time block with full availability, generates counter_proposals for conflicting users
  - FAILED      : Exceeded the maximum number of negotiation rounds

Public interface:
    summarize_meeting(meeting_id, duration_minutes=30, initiator_id=None, ...) -> dict
"""

import json
import re
from pathlib import Path
from typing import Literal

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, field_validator

from .agent_input_format import DATA_DIR
from .logger import get_logger
from config import (
    DOUBAO_API_KEY, DOUBAO_BASE_URL, DOUBAO_MODEL, LLM_TEMPERATURE,
    NEGOTIATION_TOP_K,
)

logger = get_logger("output_summary")

# ─── Configuration ────────────────────────────────────────────────────────────

SCORE_DIR = Path(__file__).resolve().parent.parent / "meeting_score"


def _score_file(meeting_id: str) -> Path:
    return SCORE_DIR / f"{meeting_id}.json"

# ─── Pydantic Models ─────────────────────────────────────────────────────────

class CoordinatorResult(BaseModel):
    """API 8 request body format."""
    decision_status: Literal["CONFIRMED", "NEGOTIATING", "FAILED"]
    final_time: str | None = None
    agent_reasoning: str
    counter_proposals: list[dict] = []

    @field_validator("final_time")
    @classmethod
    def check_final_time(cls, v: str | None) -> str | None:
        if v is None:
            return v
        pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}-\d{2}:\d{2}$"
        if not re.match(pattern, v):
            raise ValueError(f"final_time format error: {v!r}, expected YYYY-MM-DD HH:MM-HH:MM")
        return v

# ─── Time Calculation Helpers ─────────────────────────────────────────────────


def _slot_duration_minutes(start_str: str, end_str: str) -> int:
    """Calculate the number of minutes between two timestamps."""
    from datetime import datetime
    try:
        start = datetime.fromisoformat(start_str)
        end = datetime.fromisoformat(end_str)
        return max(0, int((end - start).total_seconds() / 60))
    except (ValueError, TypeError):
        return 0


def _check_duration_capacity(participants_info: list, duration_minutes: int) -> list:
    """Rule-based fallback: check if participant available time meets meeting duration."""
    insufficient = []
    for p in participants_info:
        slots = p.get("latest_slots") or []
        if not slots:
            continue
        total_minutes = sum(
            _slot_duration_minutes(s.get("start", ""), s.get("end", ""))
            for s in slots if isinstance(s, dict)
        )
        if 0 < total_minutes < duration_minutes:
            insufficient.append({
                "email": p.get("email", f"user_id={p.get('user_id')}"),
                "available_minutes": total_minutes,
            })
    return insufficient


# ─── Meeting Structure Change Detection ──────────────────────────────────────

def _detect_preference_issues(participants_info: list[dict], duration_minutes: int = 0) -> dict | None:
    """
    Use LLM to detect participant issues, including:
      1. Rejection (explicitly declined or refused to attend)
      2. Structural change (modify duration, split meeting, add/remove participants, change format, etc.)
      3. Insufficient available time (participant's total available time < required meeting duration)

    Returns {"type": "rejected"|"structural_change"|"capacity_mismatch", "detail": "..."} or None.
    """
    notes = []
    for p in participants_info:
        note = (p.get("preference_note") or "").strip()
        email = p.get("email", "unknown")
        slots = p.get("latest_slots") or []

        # Calculate participant's total available time
        total_minutes = sum(
            _slot_duration_minutes(s.get("start", ""), s.get("end", ""))
            for s in slots if isinstance(s, dict)
        )
        capacity_info = f" (available time: {total_minutes} minutes)" if total_minutes > 0 else " (no time submitted)"

        if note:
            notes.append(f"{email}{capacity_info}: {note}")
        elif duration_minutes > 0 and 0 < total_minutes < duration_minutes:
            notes.append(f"{email}{capacity_info}: [user did not add a note, but submitted available time is insufficient for the required {duration_minutes} minutes]")

    if not notes:
        return None

    llm = ChatOpenAI(
        model=DOUBAO_MODEL,
        api_key=DOUBAO_API_KEY,
        base_url=DOUBAO_BASE_URL,
        temperature=0,
    )

    duration_context = f"The required meeting duration is {duration_minutes} minutes." if duration_minutes > 0 else ""

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            f"You are a meeting coordination assistant. {duration_context}\n"
            "Analyze the following participant notes and available times, and determine if any of these three types of issues exist:\n\n"
            "1. **Rejection**: A participant explicitly states they will not attend, declines the meeting, or cannot be present.\n"
            "   Notes marked as [Rejected] are always rejections.\n"
            "   Examples: 'I won't be attending', 'I'll skip this meeting', '[Rejected] The time doesn't work'\n\n"
            "2. **Structural change**: A participant suggests modifying the meeting setup itself, not just time preferences.\n"
            "   Includes but is not limited to: changing duration, splitting the meeting, adding/removing participants, changing format (online/offline/email instead).\n"
            "   Examples: 'Suggest changing to 30 minutes', 'Suggest splitting into two sessions', 'Suggest also inviting someone', 'Let's just do this over email'\n\n"
            "3. **Insufficient available time**: A participant's total submitted available time is less than the required meeting duration.\n"
            "   This means even with negotiation, the meeting requirement cannot be met - an implicit structural issue.\n"
            "   Examples: Meeting requires 120 minutes, but participant only has 30 minutes available\n"
            "   Note: Statements like 'I can only attend for 30 minutes' or 'I only have 1 hour' also fall into this category\n\n"
            "Pure time preferences (e.g., 'I prefer afternoon', 'Friday doesn't work', 'Try to schedule in the morning') do not belong to any of the above categories.\n\n"
            "Output a single line:\n"
            "- If rejection detected: rejected: brief description (e.g., bob@x.com declined the meeting, reason: time doesn't work)\n"
            "- If structural change detected: structural_change: brief description (e.g., bob@x.com suggests changing duration to 30 minutes)\n"
            "- If insufficient time detected: capacity_mismatch: brief description (e.g., bob@x.com only has 30 minutes available, insufficient for the required 120 minutes)\n"
            "- Priority: rejected > structural_change > capacity_mismatch\n"
            "- If none detected: none"
        )),
        ("human", "Participant notes:\n{notes}"),
    ])

    try:
        result = (prompt | llm).invoke({"notes": "\n".join(notes)})
        answer = result.content.strip()
        if answer in ("none", "无") or not answer:
            return None
        if answer.startswith("rejected:"):
            return {"type": "rejected", "detail": answer[len("rejected:"):].strip()}
        if answer.startswith("structural_change:"):
            return {"type": "structural_change", "detail": answer[len("structural_change:"):].strip()}
        if answer.startswith("capacity_mismatch:"):
            return {"type": "capacity_mismatch", "detail": answer[len("capacity_mismatch:"):].strip()}
        # Fallback: treat unrecognized format as no issue
        logger.warning("preference_note detection returned unknown format: %s", answer)
        return None
    except Exception as e:
        logger.warning("preference_note LLM detection failed, skipping: %s", e)
        return None


# ─── Time Block Search ────────────────────────────────────────────────────────

def _get_initiator_slots(meeting_id: str, initiator_id: str) -> dict[str, bool]:
    """Read the initiator's date-aware time slots from meeting_time_data."""
    path = DATA_DIR / f"{meeting_id}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data.get(initiator_id, {})
    return {
        k: v for k, v in entry.items()
        if "--" in str(k)
    }


def _are_consecutive(slot_a: str, slot_b: str) -> bool:
    """Check whether two date-aware slot keys are temporally consecutive."""
    end_of_a = slot_a.split("--")[1]
    start_of_b = slot_b.split("--")[0]
    return end_of_a == start_of_b


def _slot_to_time_str(block: list[str]) -> str:
    """Convert a list of consecutive slot keys to 'YYYY-MM-DD HH:MM-HH:MM' format."""
    block_start = block[0].split("--")[0]       # "2026-03-21 10:00"
    block_end_full = block[-1].split("--")[1]   # "2026-03-21 11:00"
    block_end_hm = block_end_full.split(" ")[1] # "11:00"
    return f"{block_start}-{block_end_hm}"


def _find_candidate_blocks(
    score_data: dict,
    n_slots: int,
    initiator_slots: dict | None = None,
    total_participants: int = 1,
) -> list[dict]:
    """
    Find consecutive time blocks where all participants are available (CONFIRMED candidates).
    """
    sorted_slots = sorted(score_data.keys())
    candidates = []

    for i in range(len(sorted_slots) - n_slots + 1):
        block = sorted_slots[i: i + n_slots]

        consecutive = True
        for j in range(len(block) - 1):
            if not _are_consecutive(block[j], block[j + 1]):
                consecutive = False
                break
        if not consecutive:
            continue

        if initiator_slots is not None:
            if not all(initiator_slots.get(s) is True for s in block):
                continue

        scores = [score_data.get(s, {}).get("score", 0) for s in block]
        if min(scores) < total_participants:
            continue

        conflicts_union: set[str] = set()
        for s in block:
            conflicts_union.update(score_data.get(s, {}).get("conflict", []))

        candidates.append({
            "time": _slot_to_time_str(block),
            "min_score": min(scores),
            "total_score": sum(scores),
            "conflict_count": len(conflicts_union),
            "conflicts": sorted(conflicts_union),
        })

    candidates.sort(key=lambda x: (-x["min_score"], x["conflict_count"]))
    return candidates


def _find_negotiation_blocks(
    score_data: dict,
    n_slots: int,
    initiator_slots: dict,
    initiator_id: str,
) -> list[dict]:
    """
    Find consecutive time blocks where the initiator is available but conflicts exist (NEGOTIATING candidates).
    Sorted by ascending conflict count; ties are randomly shuffled. Returns only the top-1 result.

    Returns:
        [{"time": "2026-03-21 07:00-08:00", "conflict_count": 1, "conflicts": ["4"]}]
    """
    sorted_slots = sorted(score_data.keys())
    candidates = []

    for i in range(len(sorted_slots) - n_slots + 1):
        block = sorted_slots[i: i + n_slots]

        # Check consecutiveness
        consecutive = True
        for j in range(len(block) - 1):
            if not _are_consecutive(block[j], block[j + 1]):
                consecutive = False
                break
        if not consecutive:
            continue

        # Initiator must be available in all slots within the block
        if not all(initiator_slots.get(s) is True for s in block):
            continue

        # Collect conflicting users (excluding initiator)
        conflicts_union: set[str] = set()
        for s in block:
            conflicts_union.update(score_data.get(s, {}).get("conflict", []))
        conflicts_union.discard(initiator_id)

        # Must have conflicts (otherwise it would be CONFIRMED)
        if not conflicts_union:
            continue

        candidates.append({
            "time": _slot_to_time_str(block),
            "conflict_count": len(conflicts_union),
            "conflicts": sorted(conflicts_union),
        })

    # Fewer conflicts first; ties are randomly shuffled
    import random
    random.shuffle(candidates)  # Shuffle first to ensure random selection among equal conflict_count
    candidates.sort(key=lambda x: x["conflict_count"])
    return candidates[:1]  # Return only top-1


def _build_counter_proposals(
    negotiation_blocks: list[dict],
    participants_info: list[dict],
    initiator_id: str,
) -> list[dict]:
    """
    Build counter_proposals based on negotiation candidate blocks.

    Each conflicting user receives a suggestion containing the top-K time slots with the fewest conflicts.
    The initiator will not appear in counter_proposals.
    """
    if not negotiation_blocks:
        return []

    # Collect all conflicting user IDs
    conflict_user_ids: set[str] = set()
    for block in negotiation_blocks:
        conflict_user_ids.update(block["conflicts"])
    conflict_user_ids.discard(initiator_id)

    # Suggested time slot list
    suggested_slots = [block["time"] for block in negotiation_blocks]

    # user_id -> email mapping
    id_to_email = {
        str(p["user_id"]): p.get("email", f"user_{p['user_id']}")
        for p in participants_info
    }

    proposals = []
    for uid in sorted(conflict_user_ids):
        email = id_to_email.get(uid, uid)
        proposals.append({
            "target_email": email,
            "message": "The following are the evaluated time slots that require your coordination:",
            "suggested_slots": suggested_slots,
        })

    return proposals


# ─── Participant Summary ─────────────────────────────────────────────────────

def _build_participants_summary(
    participants_info: list[dict] | None,
    meeting_id: str,
    initiator_id: str | None,
) -> str:
    """Build a participant availability summary text for LLM to reference in reasoning."""
    if not participants_info:
        data_path = DATA_DIR / f"{meeting_id}.json"
        if not data_path.exists():
            return "(No participant information)"
        data = json.loads(data_path.read_text(encoding="utf-8"))
        lines = []
        for uid, entry in data.items():
            role = "Initiator" if uid == initiator_id else "Participant"
            free = [k for k, v in entry.items() if "--" in str(k) and v is True]
            if free:
                lines.append(f"  [{role}] user_id={uid}: available times {', '.join(sorted(free))}")
            else:
                lines.append(f"  [{role}] user_id={uid}: no clear available time")
        return "\n".join(lines)

    lines = []
    sorted_info = sorted(
        participants_info,
        key=lambda p: 0 if p.get("role") == "initiator" else 1,
    )
    for p in sorted_info:
        role = "Initiator" if p.get("role") == "initiator" else "Participant"
        email = p.get("email", f"user_id={p.get('user_id')}")
        slots = p.get("latest_slots") or []
        note = (p.get("preference_note") or "").strip()

        if slots:
            slot_strs = []
            for s in slots:
                if isinstance(s, dict) and "start" in s and "end" in s:
                    slot_strs.append(f"{s['start']}-{s['end']}")
                else:
                    slot_strs.append(f"(format error: {s})")
            lines.append(f"  [{role}] {email}: available times {', '.join(slot_strs)}")
        elif note:
            lines.append(f"  [{role}] {email}: available times (natural language) {note}")
        else:
            lines.append(f"  [{role}] {email}: no time information submitted")

    return "\n".join(lines)


# ─── LangChain Chain ──────────────────────────────────────────────────────────

def _build_confirmed_chain():
    llm = ChatOpenAI(
        model=DOUBAO_MODEL,
        api_key=DOUBAO_API_KEY,
        base_url=DOUBAO_BASE_URL,
        temperature=LLM_TEMPERATURE,
    )

    system_prompt = (
        "You are a meeting time recommendation assistant. Select the best time from candidate time blocks and output in JSON format.\n\n"
        "Input contains:\n"
        "1. Initiator information (email + available time slots)\n"
        "2. Each participant's information (email + available time slots)\n"
        "3. Candidate time block list (confirmed consecutive time slots where all participants are available)\n\n"
        "Output a single JSON object with the following fields:\n"
        "- decision_status : fixed string CONFIRMED\n"
        "- final_time      : the selected time block (use the candidate block's time field value directly)\n"
        "- agent_reasoning : the reason for selecting this time block (in English, 2-3 sentences). "
        "Must state: initiator XXX is available during XX time slot, "
        "all participants XXX are also available during this slot, no conflicts. "
        "If there are multiple candidates, explain why this one was chosen over others.\n"
        "- counter_proposals : fixed as empty array\n\n"
        "Rule: prefer the candidate with the highest min_score; if tied, choose the one with the lowest conflict_count."
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human",
         "Participant information for meeting {meeting_id}:\n\n{participants_summary}\n\n"
         "Candidate time blocks:\n\n{candidates}"),
    ])

    return prompt | llm | JsonOutputParser()


def _build_negotiating_chain():
    llm = ChatOpenAI(
        model=DOUBAO_MODEL,
        api_key=DOUBAO_API_KEY,
        base_url=DOUBAO_BASE_URL,
        temperature=LLM_TEMPERATURE,
    )

    system_prompt = (
        "You are a meeting coordination assistant. Analyze why no suitable meeting time can be found and output in JSON format.\n\n"
        "Conflict analysis is based on the initiator:\n"
        "- First list the initiator's available time slots\n"
        "- Compare each participant one by one, identify who has no overlap with the initiator's time\n"
        "- If there are previous round analysis results (previous_reasoning), avoid repeating the same suggestions\n\n"
        "Output a single JSON object with only the following two fields:\n"
        "- decision_status   : fixed string NEGOTIATING\n"
        "- agent_reasoning   : reason for negotiation failure (in English, 2-3 sentences). "
        "Must state: initiator XXX's available time is XX-XX, "
        "XXX (specific email) conflicts with the initiator's time (state their available time), "
        "therefore no time slot where all participants are available can be found.\n\n"
        "Note: do not output final_time and counter_proposals fields, these are filled automatically by the system."
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", (
            "Meeting {meeting_id} cannot find time blocks that satisfy all conditions.\n"
            "Required meeting duration: {duration_minutes} minutes\n"
            "Current negotiation round: {round_count}\n\n"
            "Participant information:\n{participants_summary}\n\n"
            "Initiator available time slots:\n{initiator_slots}\n\n"
            "Candidate time blocks with fewest conflicts (initiator available but some participants conflict):\n{negotiation_blocks}\n\n"
            "Previous round analysis result:\n{previous_reasoning}"
        )),
    ])

    return prompt | llm | JsonOutputParser()

# ─── Public API ───────────────────────────────────────────────────────────────

def summarize_meeting(
    meeting_id: str,
    duration_minutes: int = 30,
    initiator_id: str | None = None,
    total_participants: int | None = None,
    participants_info: list[dict] | None = None,
    meeting_date: str | None = None,
    round_count: int = 0,
    max_rounds: int = 3,
    previous_reasoning: str | None = None,
) -> dict:
    """
    Analyze meeting scoring data and return an API 8 format request body.

    Decision logic:
      1. round_count >= max_rounds -> FAILED
      2. Found consecutive block with full availability -> CONFIRMED
      3. Otherwise -> NEGOTIATING (with counter_proposals)
    """
    # ── FAILED: exceeded maximum negotiation rounds ──────────────────────────
    if round_count >= max_rounds:
        return {
            "decision_status": "FAILED",
            "final_time": None,
            "agent_reasoning": f"After {max_rounds} rounds of negotiation, participants still could not agree on a meeting time.",
            "counter_proposals": [],
        }

    # ── FAILED: LLM unified detection (rejection / structural change / insufficient time) ──
    if participants_info:
        issue = _detect_preference_issues(participants_info, duration_minutes)
        if issue:
            if issue["type"] == "rejected":
                logger.info("LLM detected participant rejection, returning FAILED: %s", issue["detail"])
                return {
                    "decision_status": "FAILED",
                    "final_time": None,
                    "agent_reasoning": f"A participant declined the meeting invitation: {issue['detail']}",
                    "counter_proposals": [],
                }
            elif issue["type"] == "structural_change":
                logger.info("LLM detected meeting structural change request, returning FAILED: %s", issue["detail"])
                return {
                    "decision_status": "FAILED",
                    "final_time": None,
                    "agent_reasoning": f"A participant suggested meeting structural adjustments; the current meeting setup needs the initiator to reconfirm: {issue['detail']}",
                    "counter_proposals": [],
                }
            elif issue["type"] == "capacity_mismatch":
                logger.info("LLM detected insufficient participant available time: %s", issue["detail"])
                return {
                    "decision_status": "FAILED",
                    "final_time": None,
                    "agent_reasoning": f"Participant available time is insufficient to meet the meeting requirement: {issue['detail']}. It is recommended that the initiator adjust the meeting duration or coordinate with participants.",
                    "counter_proposals": [],
                }

    # ── Rule-based fallback: participant available time insufficient ────────
    if participants_info:
        insufficient = _check_duration_capacity(participants_info, duration_minutes)
        if insufficient:
            detail = "; ".join(f"{p['email']} only has {p['available_minutes']} minutes" for p in insufficient)
            logger.info("[%s] Rule-based fallback - participant available time insufficient: %s", meeting_id, detail)
            return {
                "decision_status": "FAILED",
                "final_time": None,
                "agent_reasoning": f"The meeting requires {duration_minutes} minutes, but the following participants have insufficient available time: {detail}. It is recommended that the initiator adjust the meeting duration or coordinate with participants.",
                "counter_proposals": [],
            }

    path = _score_file(meeting_id)
    if not path.exists():
        raise FileNotFoundError(
            f"Score file not found: {path}, please call score_meeting('{meeting_id}') first"
        )

    score_data: dict = json.loads(path.read_text(encoding="utf-8"))
    n_slots = max(1, (duration_minutes + 29) // 30)
    logger.info("summarize_meeting: meeting=%s, duration=%dmin, n_slots=%d, initiator=%s",
                meeting_id, duration_minutes, n_slots, initiator_id)

    # Read initiator's time slots
    initiator_slots: dict | None = None
    if initiator_id is not None:
        initiator_slots = _get_initiator_slots(meeting_id, initiator_id)
        initiator_true = sum(1 for v in initiator_slots.values() if v is True)
        logger.debug("Initiator available slot count: %d", initiator_true)

    # Automatically infer total number of participants
    if total_participants is None:
        data_path = DATA_DIR / f"{meeting_id}.json"
        if data_path.exists():
            data = json.loads(data_path.read_text(encoding="utf-8"))
            total_participants = len(data)
        else:
            total_participants = 1
    logger.info("Total number of participants: %d", total_participants)

    # Find CONFIRMED candidate blocks
    candidates = _find_candidate_blocks(
        score_data, n_slots, initiator_slots, total_participants
    )
    logger.info("CONFIRMED candidate block count: %d", len(candidates))
    for c in candidates[:5]:
        logger.debug("  Candidate: %s (min_score=%d, conflict=%d)", c["time"], c["min_score"], c["conflict_count"])

    # Build participant summary
    participants_summary = _build_participants_summary(
        participants_info, meeting_id, initiator_id
    )

    if candidates:
        # ── CONFIRMED ────────────────────────────────────────────────────────
        logger.info("[LLM Agent] Invoking CONFIRMED chain...")
        raw: dict = _build_confirmed_chain().invoke({
            "meeting_id": meeting_id,
            "participants_summary": participants_summary,
            "candidates": json.dumps(candidates[:5], ensure_ascii=False, indent=2),
        })
        raw.setdefault("decision_status", "CONFIRMED")
        raw.setdefault("counter_proposals", [])
        logger.info("[LLM Agent] CONFIRMED chain returned: %s", json.dumps(raw, ensure_ascii=False))
        result = CoordinatorResult.model_validate(raw)
        return result.model_dump()

    # ── NEGOTIATING ──────────────────────────────────────────────────────────
    logger.info("No CONFIRMED candidate blocks, entering NEGOTIATING flow")

    # Find candidate blocks with fewest conflicts (initiator available but some participants conflict)
    negotiation_blocks = _find_negotiation_blocks(
        score_data, n_slots, initiator_slots or {},
        initiator_id or "",
    )

    logger.info("Negotiation candidate blocks (top-1): %s", json.dumps(negotiation_blocks, ensure_ascii=False))

    # Initiator available time
    initiator_free = (
        sorted([k for k, v in initiator_slots.items() if v is True])
        if initiator_slots else []
    )

    # LLM generates reasoning
    logger.info("[LLM Agent] Invoking NEGOTIATING chain...")
    raw: dict = _build_negotiating_chain().invoke({
        "meeting_id": meeting_id,
        "duration_minutes": duration_minutes,
        "round_count": round_count,
        "participants_summary": participants_summary,
        "initiator_slots": json.dumps(initiator_free, ensure_ascii=False),
        "negotiation_blocks": json.dumps(negotiation_blocks, ensure_ascii=False, indent=2),
        "previous_reasoning": previous_reasoning or "(First round of negotiation, no previous records)",
    })

    logger.info("[LLM Agent] NEGOTIATING chain returned: %s", json.dumps(raw, ensure_ascii=False))

    # Build counter_proposals (programmatic logic, no LLM dependency)
    counter_proposals = _build_counter_proposals(
        negotiation_blocks,
        participants_info or [],
        initiator_id or "",
    )

    logger.info("counter_proposals: %d users", len(counter_proposals))
    for cp in counter_proposals:
        logger.debug("  → %s: %s", cp["target_email"], cp["suggested_slots"])

    result = CoordinatorResult.model_validate({
        "decision_status": "NEGOTIATING",
        "final_time": None,
        "agent_reasoning": raw.get("agent_reasoning", "Unable to find a time slot where all participants are available."),
        "counter_proposals": counter_proposals,
    })
    return result.model_dump()
