#!/usr/bin/env python3
"""
Test coordinate_from_task: simulate API 7 input, verify API 8 output.
Contains 4 scenarios: 2 no-conflict (CONFIRMED) + 2 with-conflict (NEGOTIATING).
"""
import json
from utils import coordinate_from_task

# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 1: No conflict / 30-minute meeting
#   Alice (initiator)  : 18:00-20:00
#   Bob  (participant)  : 18:00-19:00
#   Carol (participant) : Natural language "Free all evening"
#   -> All three available 18:00-19:00, 30 min sufficient, expected CONFIRMED
# ═══════════════════════════════════════════════════════════════════════════════
task_1 = {
    "meeting_id": "test_no_conflict_30min",
    "title": "Scenario 1: No conflict 30 min",
    "duration_minutes": 30,
    "round_count": 0,
    "participants_data": [
        {
            "user_id": 1,
            "email": "alice@example.com",
            "role": "initiator",
            "latest_slots": [
                {"start": "2026-03-18 18:00", "end": "2026-03-18 20:00"}
            ],
            "preference_note": None,
        },
        {
            "user_id": 2,
            "email": "bob@example.com",
            "role": "participant",
            "latest_slots": [
                {"start": "2026-03-18 18:00", "end": "2026-03-18 19:00"}
            ],
            "preference_note": None,
        },
        {
            "user_id": 3,
            "email": "carol@example.com",
            "role": "participant",
            "latest_slots": [],
            "preference_note": "Free all evening",
        },
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 2: No conflict / 60-minute meeting (needs 2 consecutive slots)
#   Alice (initiator)  : 14:00-17:00
#   Bob  (participant)  : 15:00-17:00
#   Carol (participant) : 14:30-18:00
#   -> All three available 15:00-17:00, 60-min consecutive block exists, expected CONFIRMED
# ═══════════════════════════════════════════════════════════════════════════════
task_2 = {
    "meeting_id": "test_no_conflict_60min",
    "title": "Scenario 2: No conflict 60 min",
    "duration_minutes": 60,
    "round_count": 0,
    "participants_data": [
        {
            "user_id": 1,
            "email": "alice@example.com",
            "role": "initiator",
            "latest_slots": [
                {"start": "2026-03-18 14:00", "end": "2026-03-18 17:00"}
            ],
            "preference_note": None,
        },
        {
            "user_id": 2,
            "email": "bob@example.com",
            "role": "participant",
            "latest_slots": [
                {"start": "2026-03-18 15:00", "end": "2026-03-18 17:00"}
            ],
            "preference_note": None,
        },
        {
            "user_id": 3,
            "email": "carol@example.com",
            "role": "participant",
            "latest_slots": [
                {"start": "2026-03-18 14:30", "end": "2026-03-18 18:00"}
            ],
            "preference_note": None,
        },
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 3: Conflict / no time overlap at all
#   Alice (initiator)  : 09:00-11:00 (morning)
#   Bob  (participant)  : 18:00-20:00 (evening)
#   Carol (participant) : 14:00-16:00 (afternoon)
#   -> No intersection among the three, expected NEGOTIATING
# ═══════════════════════════════════════════════════════════════════════════════
task_3 = {
    "meeting_id": "test_conflict_no_overlap",
    "title": "Scenario 3: No overlap at all",
    "duration_minutes": 30,
    "round_count": 0,
    "participants_data": [
        {
            "user_id": 1,
            "email": "alice@example.com",
            "role": "initiator",
            "latest_slots": [
                {"start": "2026-03-18 09:00", "end": "2026-03-18 11:00"}
            ],
            "preference_note": None,
        },
        {
            "user_id": 2,
            "email": "bob@example.com",
            "role": "participant",
            "latest_slots": [
                {"start": "2026-03-18 18:00", "end": "2026-03-18 20:00"}
            ],
            "preference_note": None,
        },
        {
            "user_id": 3,
            "email": "carol@example.com",
            "role": "participant",
            "latest_slots": [
                {"start": "2026-03-18 14:00", "end": "2026-03-18 16:00"}
            ],
            "preference_note": None,
        },
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 4: Conflict / intersection time not long enough (duration too large)
#   Alice (initiator)  : 18:00-19:00
#   Bob  (participant)  : 18:00-19:00
#   Carol (participant) : 18:00-19:00
#   -> All three available 18:00-19:00 (60 min), but meeting needs 90 min
#   -> Only 2 consecutive slots (60 min), insufficient for 3 slots (90 min), expected NEGOTIATING
# ═══════════════════════════════════════════════════════════════════════════════
task_4 = {
    "meeting_id": "test_conflict_duration_too_long",
    "title": "Scenario 4: Insufficient overlap",
    "duration_minutes": 90,
    "round_count": 0,
    "participants_data": [
        {
            "user_id": 1,
            "email": "alice@example.com",
            "role": "initiator",
            "latest_slots": [
                {"start": "2026-03-18 18:00", "end": "2026-03-18 19:00"}
            ],
            "preference_note": None,
        },
        {
            "user_id": 2,
            "email": "bob@example.com",
            "role": "participant",
            "latest_slots": [
                {"start": "2026-03-18 18:00", "end": "2026-03-18 19:00"}
            ],
            "preference_note": None,
        },
        {
            "user_id": 3,
            "email": "carol@example.com",
            "role": "participant",
            "latest_slots": [
                {"start": "2026-03-18 18:00", "end": "2026-03-18 19:00"}
            ],
            "preference_note": None,
        },
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════
# Run all scenarios
# ═══════════════════════════════════════════════════════════════════════════════

tasks = [task_1, task_2, task_3, task_4]

for i, task in enumerate(tasks, 1):
    print(f"\n{'='*60}")
    print(f"  Scenario {i}: {task['title']}")
    print(f"  meeting_id: {task['meeting_id']}")
    print(f"  duration_minutes: {task['duration_minutes']}")
    print(f"{'='*60}\n")

    result = coordinate_from_task(task)

    print(f"\n  Result:")
    print(json.dumps(result, ensure_ascii=False, indent=4))

    # Simple assertion
    expected = "CONFIRMED" if i <= 2 else "NEGOTIATING"
    status = result.get("decision_status", "UNKNOWN")
    ok = "PASS" if status == expected else "FAIL"
    print(f"\n  [{ok}] Expected {expected}, actual {status}")
    print()
