#!/usr/bin/env python3
"""
user_time_format / submit_user_time API
Converts user time descriptions to structured JSON, supporting two input formats:
  1. Standard API format: available_slots list, each containing start/end (YYYY-MM-DD HH:MM)
  2. Natural language: parsed by LLM Agent

Time slot key format: YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM (double-dash separated)
Only stores time slots actually mentioned by the user (True / False), no longer fills a fixed 36 slots.

Each meeting_id corresponds to meeting_time_data/{meeting_id}.json, multiple users in the same meeting share one file.

Public interface:
    user_time_format(user_input, user_id, meeting_id, reference_date) -> dict
    submit_user_time(user_input, user_id, meeting_id, reference_date) -> dict
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from typing import Literal, Union
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from config import DOUBAO_API_KEY, DOUBAO_BASE_URL, DOUBAO_MODEL, LLM_TEMPERATURE
from utils.logger import get_logger

logger = get_logger("agent_input_format")

# ─── Configuration ────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).resolve().parent.parent / "meeting_time_data"

# Date-aware slot key regex: YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM
_DATED_SLOT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}--\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"
)

# Legacy format compatibility (for LLM output): HH:MM-HH:MM
_SLOT_RE = re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")


def _meeting_file(meeting_id: str) -> Path:
    """Return the JSON file path for the specified meeting, ensuring the parent directory exists."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{meeting_id}.json"


def _gen_slots() -> list[str]:
    """Generate 30-minute time slots from 06:00 to 00:00 (next day) in HH:MM-HH:MM format (LLM reference only)."""
    slots = []
    for h in range(6, 24):
        for m in (0, 30):
            end_total = h * 60 + m + 30
            end_h, end_m = divmod(end_total % (24 * 60), 60)
            slots.append(f"{h:02d}:{m:02d}-{end_h:02d}:{end_m:02d}")
    return slots


# Reference time slot list used during LLM natural language parsing
TIME_SLOTS: list[str] = _gen_slots()

# ─── Pydantic Models ─────────────────────────────────────────────────────────

SlotValue = Union[bool, Literal["other"]]


class AvailabilityOutput(BaseModel):
    """LLM output structure: HH:MM-HH:MM -> True/False/"other" mapping (for natural language parsing)."""

    slots: dict[str, SlotValue] = Field(
        description=(
            "Availability mapping for all time slots. "
            "Key format must be 'HH:MM-HH:MM' (e.g., '18:00-18:30'), "
            "value can only be one of three values:\n"
            "  true   -- User explicitly stated available in this time slot\n"
            "  false  -- User explicitly stated unavailable in this time slot\n"
            "  \"other\" -- User did not mention this time slot"
        )
    )

    @field_validator("slots")
    @classmethod
    def check_key_format(cls, v: dict[str, SlotValue]) -> dict[str, SlotValue]:
        bad_keys = [k for k in v if not _SLOT_RE.match(k)]
        if bad_keys:
            raise ValueError(f"Time slot key format error (expected HH:MM-HH:MM): {bad_keys}")
        bad_vals = [k for k, val in v.items() if not isinstance(val, bool) and val != "other"]
        if bad_vals:
            raise ValueError(f"Illegal values (only true/false/\"other\" allowed), problematic fields: {bad_vals}")
        return v

    @model_validator(mode="after")
    def check_all_slots_present(self) -> "AvailabilityOutput":
        missing = [s for s in TIME_SLOTS if s not in self.slots]
        if missing:
            raise ValueError(
                f"Output missing {len(missing)} time slots, "
                f"e.g.: {missing[:3]}{'...' if len(missing) > 3 else ''}"
            )
        return self


class RoleEntry(BaseModel):
    """
    A single user's complete entry in a meeting.

    Fixed metadata fields: user_ID, meeting_ID
    Dynamic time slot fields: attached via extra="allow"
      Key format is YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM
    """

    model_config = ConfigDict(extra="allow")

    user_ID: str = Field(description="User unique identifier")
    meeting_ID: str = Field(description="Meeting unique identifier")

    @model_validator(mode="after")
    def check_extra_keys(self) -> "RoleEntry":
        bad = [
            k for k in (self.model_extra or {})
            if not _DATED_SLOT_RE.match(k)
        ]
        if bad:
            raise ValueError(f"Illegal key in RoleEntry (expected YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM): {bad}")
        return self

    def get_slots(self) -> dict[str, SlotValue]:
        return dict(self.model_extra or {})


class AvailabilityStore(RootModel[dict[str, RoleEntry]]):
    """Complete storage structure for a single meeting JSON file."""

# ─── Data Layer ──────────────────────────────────────────────────────────────

def _load_store(meeting_id: str) -> AvailabilityStore:
    path = _meeting_file(meeting_id)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return AvailabilityStore.model_validate(raw)
    return AvailabilityStore.model_validate({})


def _save_store(store: AvailabilityStore, meeting_id: str) -> None:
    path = _meeting_file(meeting_id)
    path.write_text(
        json.dumps(store.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

# ─── Standard API Format Parsing ─────────────────────────────────────────────

_STANDARD_SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def _is_standard_slots(user_input: object) -> bool:
    """Check if input is standard API format: list[dict] with each item containing start/end (YYYY-MM-DD HH:MM)."""
    if not isinstance(user_input, list) or len(user_input) == 0:
        return False
    first = user_input[0]
    return (
        isinstance(first, dict)
        and "start" in first
        and "end" in first
        and _STANDARD_SLOT_RE.match(str(first.get("start", "")))
    )


def _parse_standard_slots(available_slots: list[dict]) -> dict[str, bool]:
    """
    Convert standard API format time slot list to date-aware 30-minute time slots.
    Only generates True slots (times when user is available).

    Args:
        available_slots: [{"start": "2026-03-21 10:00", "end": "2026-03-21 12:00"}, ...]

    Returns:
        {"2026-03-21 10:00--2026-03-21 10:30": True, "2026-03-21 10:30--2026-03-21 11:00": True, ...}
    """
    result: dict[str, bool] = {}

    for item in available_slots:
        start = datetime.strptime(str(item["start"]), "%Y-%m-%d %H:%M")
        end = datetime.strptime(str(item["end"]), "%Y-%m-%d %H:%M")

        current = start
        while current + timedelta(minutes=30) <= end:
            next_time = current + timedelta(minutes=30)
            key = f"{current.strftime('%Y-%m-%d %H:%M')}--{next_time.strftime('%Y-%m-%d %H:%M')}"
            result[key] = True
            current = next_time

    logger.debug("Standard format parsing: %d intervals -> %d True slots", len(available_slots), len(result))
    return result


# ─── LangChain Chain (Natural Language Parsing) ──────────────────────────────

def _build_chain():
    llm = ChatOpenAI(
        model=DOUBAO_MODEL,
        api_key=DOUBAO_API_KEY,
        base_url=DOUBAO_BASE_URL,
        temperature=LLM_TEMPERATURE,
    )
    structured_llm = llm.with_structured_output(AvailabilityOutput, method="function_calling")

    slots_text = "\n".join(f"  {s}" for s in TIME_SLOTS)

    system_prompt = f"""You are a time scheduling assistant responsible for converting user natural language descriptions into time availability data.

Available time slot list (24-hour format, 30 minutes each, from 06:00 to 00:00 next day):
{slots_text}

Task: Based on user input, fill in a tri-state value for each time slot in slots, output in the required JSON format.

Rules:
1. All time slots must appear in slots, none may be omitted
2. Time slots the user explicitly mentions as "available/free/can do" -> true
3. Time slots the user explicitly mentions as "unavailable/busy/can't/occupied" -> false
4. Time slots the user did not mention -> "other"
5. "tonight" -> after 18:00; "morning" -> 06:00-12:00; "afternoon" -> 12:00-18:00
6. Minimum time granularity is 30 minutes"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{user_input}"),
    ])

    return prompt | structured_llm


def _convert_llm_slots_to_dated(
    llm_slots: dict[str, SlotValue],
    reference_date: str,
) -> dict[str, bool]:
    """
    Convert LLM output HH:MM-HH:MM slots to date-aware format.
    Only keeps True and False slots, discards "other".

    Args:
        llm_slots: {"18:00-18:30": True, "18:30-19:00": False, "19:00-19:30": "other", ...}
        reference_date: "2026-03-21"

    Returns:
        {"2026-03-21 18:00--2026-03-21 18:30": True, "2026-03-21 18:30--2026-03-21 19:00": False, ...}
    """
    result: dict[str, bool] = {}
    for slot_key, val in llm_slots.items():
        if val == "other":
            continue
        parts = slot_key.split("-")
        start_hm = parts[0]
        end_hm = parts[1]
        key = f"{reference_date} {start_hm}--{reference_date} {end_hm}"
        result[key] = val
    return result


# ─── Public API ───────────────────────────────────────────────────────────────

def user_time_format(
    user_input: str,
    user_id: str,
    meeting_id: str,
    reference_date: str | None = None,
) -> dict:
    """
    Convert user natural language time description to structured availability data and persist it.

    Args:
        user_input:     Natural language time description
        user_id:        User unique identifier
        meeting_id:     Meeting unique identifier
        reference_date: Reference date "YYYY-MM-DD", used to generate date-aware slot keys

    Returns:
        The user's complete entry dict
    """
    logger.info("[LLM Agent] Natural language parsing user_id=%s, meeting=%s, input=%r", user_id, meeting_id, user_input)
    output: AvailabilityOutput = _build_chain().invoke({"user_input": user_input})
    true_count = sum(1 for v in output.slots.values() if v is True)
    false_count = sum(1 for v in output.slots.values() if v is False)
    logger.info("[LLM Agent] Parse result: True=%d, False=%d, other=%d", true_count, false_count, len(output.slots) - true_count - false_count)

    if reference_date:
        slots = _convert_llm_slots_to_dated(output.slots, reference_date)
    else:
        slots = {k: v for k, v in output.slots.items() if v != "other"}

    entry = RoleEntry(user_ID=user_id, meeting_ID=meeting_id, **slots)
    store = _load_store(meeting_id)
    store.root[user_id] = entry
    _save_store(store, meeting_id)
    logger.debug("Saved user_id=%s to meeting_time_data/%s.json, slot_count=%d", user_id, meeting_id, len(slots))
    return entry.model_dump()


def submit_user_time(
    user_input: "str | list[dict]",
    user_id: str,
    meeting_id: str,
    reference_date: str | None = None,
) -> dict:
    """
    Unified entry point: automatically detects input format and stores user time data.

    Args:
        user_input:     list[dict] standard API format or str natural language
        user_id:        User unique identifier
        meeting_id:     Meeting unique identifier
        reference_date: Reference date (used for natural language parsing)

    Returns:
        The user's complete entry dict
    """
    if _is_standard_slots(user_input):
        logger.info("submit_user_time: user=%s, meeting=%s, standard format (%d intervals)", user_id, meeting_id, len(user_input))
        slots = _parse_standard_slots(user_input)  # type: ignore[arg-type]
    else:
        logger.info("[LLM Agent] submit_user_time: user=%s, meeting=%s, natural_language=%r", user_id, meeting_id, user_input)
        output: AvailabilityOutput = _build_chain().invoke(
            {"user_input": str(user_input)}
        )
        true_count = sum(1 for v in output.slots.values() if v is True)
        false_count = sum(1 for v in output.slots.values() if v is False)
        logger.info("[LLM Agent] Parse result: True=%d, False=%d, other=%d", true_count, false_count, len(output.slots) - true_count - false_count)
        if reference_date:
            slots = _convert_llm_slots_to_dated(output.slots, reference_date)
        else:
            slots = {k: v for k, v in output.slots.items() if v != "other"}

    entry = RoleEntry(user_ID=user_id, meeting_ID=meeting_id, **slots)
    store = _load_store(meeting_id)
    store.root[user_id] = entry
    _save_store(store, meeting_id)
    logger.debug("Saved user=%s -> meeting_time_data/%s.json, slot_count=%d", user_id, meeting_id, len(slots))
    return entry.model_dump()
