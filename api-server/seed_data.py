"""
Seed data script - Pre-populate test data for Agent / Plugin team integration testing

Usage:
  python seed_data.py

Note: No need to manually delete the database before running; the script will automatically clear and rebuild it.

Pre-populated data:
  - 5 users (alice/bob/carol/dave/eve), with fixed tokens for easy integration testing
  - 4 meetings in different states:
    * mtg_seed_001: COLLECTING  -> Plugin test: submit time
    * mtg_seed_002: ANALYZING   -> Agent test: no conflict -> CONFIRMED
    * mtg_seed_003: ANALYZING   -> Agent test: conflict -> NEGOTIATING
    * mtg_seed_004: CONFIRMED   -> Query completed meeting
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from app.models.database import Base, engine, SessionLocal, User, Meeting, NegotiationLog

# ========== Initialize Database ==========

print("Clearing and rebuilding database...")
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

db = SessionLocal()

try:
    # ========== 1. Create users (fixed tokens for easy integration testing) ==========

    print("\n[1/5] Creating users...")

    users_data = [
        {"email": "alice@example.com", "token": "sk-seed-alice"},
        {"email": "bob@example.com",   "token": "sk-seed-bob"},
        {"email": "carol@example.com", "token": "sk-seed-carol"},
        {"email": "dave@example.com",  "token": "sk-seed-dave"},
        {"email": "eve@example.com",   "token": "sk-seed-eve"},
    ]

    users = {}
    for u in users_data:
        user = User(
            email=u["email"],
            token=u["token"],
            created_at=datetime.utcnow()
        )
        db.add(user)
        db.flush()
        users[u["email"]] = user
        print(f"  [user] {u['email']:25s} token={u['token']:20s} id={user.id}")

    db.commit()

    # ========== 2. Meeting 1: COLLECTING status ==========

    print("\n[2/5] Creating meeting 1: COLLECTING (waiting for submissions)...")

    mtg1 = Meeting(
        id="mtg_seed_001",
        initiator_id=users["alice@example.com"].id,
        title="Sprint 14 Planning Meeting",
        duration_minutes=60,
        status="COLLECTING",
        round_count=0,
        created_at=datetime.utcnow()
    )
    db.add(mtg1)

    # alice (initiator, already submitted)
    db.add(NegotiationLog(
        meeting_id="mtg_seed_001",
        user_id=users["alice@example.com"].id,
        role="initiator",
        latest_slots=["2026-03-20 09:00-12:00", "2026-03-20 14:00-17:00"],
        preference_note="Morning preferred, afternoon also works",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # bob (pending submission)
    db.add(NegotiationLog(
        meeting_id="mtg_seed_001",
        user_id=users["bob@example.com"].id,
        role="participant",
        latest_slots=[],
        preference_note=None,
        action_required=True,
        created_at=datetime.utcnow()
    ))

    # carol (pending submission)
    db.add(NegotiationLog(
        meeting_id="mtg_seed_001",
        user_id=users["carol@example.com"].id,
        role="participant",
        latest_slots=[],
        preference_note=None,
        action_required=True,
        created_at=datetime.utcnow()
    ))

    print("  done: mtg_seed_001 [COLLECTING] - alice initiated, bob/carol pending")

    # ========== 3. Meeting 2: ANALYZING status ==========

    print("\n[3/5] Creating meeting 2: ANALYZING no conflict (Agent should return CONFIRMED)...")

    mtg2 = Meeting(
        id="mtg_seed_002",
        initiator_id=users["dave@example.com"].id,
        title="Technical Review",
        duration_minutes=30,
        status="ANALYZING",
        round_count=0,
        created_at=datetime.utcnow()
    )
    db.add(mtg2)

    # dave (initiator, already submitted)
    db.add(NegotiationLog(
        meeting_id="mtg_seed_002",
        user_id=users["dave@example.com"].id,
        role="initiator",
        latest_slots=["2026-03-21 10:00-12:00", "2026-03-21 14:00-16:00"],
        preference_note="Preferably in the morning",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # alice (already submitted)
    db.add(NegotiationLog(
        meeting_id="mtg_seed_002",
        user_id=users["alice@example.com"].id,
        role="participant",
        latest_slots=["2026-03-21 10:00-11:00", "2026-03-21 15:00-17:00"],
        preference_note="10am to 11am is best",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # eve (already submitted)
    db.add(NegotiationLog(
        meeting_id="mtg_seed_002",
        user_id=users["eve@example.com"].id,
        role="participant",
        latest_slots=["2026-03-21 09:00-12:00"],
        preference_note="Only available in the morning",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    print("  done: mtg_seed_002 [ANALYZING] no conflict - dave initiated, alice/eve submitted")
    print("    -> Agent should return CONFIRMED (all three available at 10:00-11:00)")

    # ========== 4. Meeting 3: ANALYZING + conflict ==========

    print("\n[4/5] Creating meeting 3: ANALYZING (conflict, Agent needs to generate compromise)...")

    mtg3_conflict = Meeting(
        id="mtg_seed_003",
        initiator_id=users["alice@example.com"].id,
        title="Product Requirements Alignment",
        duration_minutes=60,
        status="ANALYZING",
        round_count=0,
        created_at=datetime.utcnow()
    )
    db.add(mtg3_conflict)

    # alice: morning only
    db.add(NegotiationLog(
        meeting_id="mtg_seed_003",
        user_id=users["alice@example.com"].id,
        role="initiator",
        latest_slots=["2026-03-21 09:00-12:00"],
        preference_note="Only available in the morning, client visit in the afternoon",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # bob: afternoon only
    db.add(NegotiationLog(
        meeting_id="mtg_seed_003",
        user_id=users["bob@example.com"].id,
        role="participant",
        latest_slots=["2026-03-21 14:00-18:00"],
        preference_note="Have class in the morning, can only attend in the afternoon",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # carol: available all day
    db.add(NegotiationLog(
        meeting_id="mtg_seed_003",
        user_id=users["carol@example.com"].id,
        role="participant",
        latest_slots=["2026-03-21 09:00-12:00", "2026-03-21 14:00-18:00"],
        preference_note="Available all day, flexible with everyone's schedule",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    print("  done: mtg_seed_003 [ANALYZING] conflict - alice(morning) vs bob(afternoon) vs carol(all day)")
    print("    -> Agent should return NEGOTIATING + counter_proposals")
    print("    -> alice and bob have no time overlap, compromise needed")

    # ========== 5. Meeting 4: CONFIRMED status ==========

    print("\n[5/5] Creating meeting 4: CONFIRMED (completed)...")

    mtg3 = Meeting(
        id="mtg_seed_004",
        initiator_id=users["bob@example.com"].id,
        title="Code Review Weekly",
        duration_minutes=45,
        status="CONFIRMED",
        final_time="2026-03-19 15:00-15:45",
        round_count=0,
        coordinator_reasoning="All participants are available Wednesday afternoon 15:00-16:00, selecting 45 minutes as meeting time.",
        created_at=datetime.utcnow()
    )
    db.add(mtg3)

    for email, role in [
        ("bob@example.com", "initiator"),
        ("alice@example.com", "participant"),
        ("dave@example.com", "participant")
    ]:
        db.add(NegotiationLog(
            meeting_id="mtg_seed_004",
            user_id=users[email].id,
            role=role,
            latest_slots=["2026-03-19 14:00-17:00"],
            preference_note=None,
            action_required=False,
            created_at=datetime.utcnow()
        ))

    print("  done: mtg_seed_004 [CONFIRMED] - bob initiated, final_time=2026-03-19 15:00-15:45")

    db.commit()

    # ========== Output Summary ==========

    print(f"\n{'=' * 60}")
    print("  Seed data created successfully!")
    print(f"{'=' * 60}")

    print("\n  User Tokens (fixed values, can be copied directly):")
    print("  +---------------------------+---------------------+----+")
    print("  | Email                     | Token               | ID |")
    print("  +---------------------------+---------------------+----+")
    for email, user in users.items():
        print(f"  | {email:24s}  | {user.token:18s}  | {user.id:<2d} |")
    print("  +---------------------------+---------------------+----+")

    print()
    print("  Meeting List:")
    print("  +------------------+------------+----------------------------+-------------------------------+")
    print("  | Meeting ID       | Status     | Title                      | Test Purpose                  |")
    print("  +------------------+------------+----------------------------+-------------------------------+")
    print("  | mtg_seed_001     | COLLECTING | Sprint 14 Planning Meeting | Plugin: bob/carol submit time |")
    print("  | mtg_seed_002     | ANALYZING  | Technical Review           | Agent: no conflict->CONFIRMED |")
    print("  | mtg_seed_003     | ANALYZING  | Product Req Alignment      | Agent: conflict->NEGOTIATING  |")
    print("  | mtg_seed_004     | CONFIRMED  | Code Review Weekly         | Query completed meeting       |")
    print("  +------------------+------------+----------------------------+-------------------------------+")

    print()
    print("  ========== Agent Team Test Steps ==========")
    print()
    print("  Step 1: Poll pending coordination tasks")
    print("    GET /api/agent/tasks/pending")
    print("    -> Should return mtg_seed_002 and mtg_seed_003 as two tasks")
    print()
    print("  Step 2: Submit CONFIRMED for no-conflict meeting")
    print("    POST /api/agent/meetings/mtg_seed_002/result")
    print('    body: {"decision_status":"CONFIRMED",')
    print('           "final_time":"2026-03-21 10:00-10:30",')
    print('           "agent_reasoning":"All three available at 10:00-11:00",')
    print('           "counter_proposals":[]}')
    print()
    print("  Step 3: Submit NEGOTIATING for conflicting meeting")
    print("    POST /api/agent/meetings/mtg_seed_003/result")
    print('    body: {"decision_status":"NEGOTIATING",')
    print('           "final_time":null,')
    print('           "agent_reasoning":"alice only morning, bob only afternoon, complete conflict",')
    print('           "counter_proposals":[')
    print('             {"target_email":"alice@example.com","message":"bob is only available in the afternoon, can you adjust to 13:00?"},')
    print('             {"target_email":"bob@example.com","message":"alice is only available in the morning, can you adjust to 12:00?"}')
    print("           ]}")
    print()
    print("  ========== Plugin Team Test Steps ==========")
    print()
    print("  Step 1: Query pending tasks with bob's token")
    print("    GET /api/tasks/pending?token=sk-seed-bob")
    print("    -> Should see mtg_seed_001 requires time submission")
    print()
    print("  Step 2: bob submits time")
    print("    POST /api/meetings/mtg_seed_001/submit?token=sk-seed-bob")
    print('    body: {"response_type":"INITIAL",')
    print('           "available_slots":["2026-03-20 10:00-12:00"],')
    print('           "preference_note":"morning available"}')
    print()
    print("  Step 3: carol submits time (triggers ANALYZING)")
    print("    POST /api/meetings/mtg_seed_001/submit?token=sk-seed-carol")
    print('    body: {"response_type":"INITIAL",')
    print('           "available_slots":["2026-03-20 14:00-16:00"]}')
    print()

    # ========== Output Integration Guide File ==========

    guide_content = f"""# Meeting Coordinator - Integration Testing Guide

> Auto-generated by seed_data.py at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

## Service URLs

- API Base URL: http://192.168.22.28:8000
- Swagger Docs: http://192.168.22.28:8000/docs
- Health Check:  http://192.168.22.28:8000/health

## Authentication

All authenticated endpoints support two methods for passing Token:

1. **Header method** (recommended for production)
   ```
   Authorization: Bearer sk-seed-alice
   ```

2. **Query parameter method** (recommended for Swagger UI testing)
   ```
   GET /api/tasks/pending?token=sk-seed-alice
   ```

---

## Pre-populated Users

| Email | Token | User ID |
|-------|-------|---------|
| alice@example.com | `sk-seed-alice` | 1 |
| bob@example.com | `sk-seed-bob` | 2 |
| carol@example.com | `sk-seed-carol` | 3 |
| dave@example.com | `sk-seed-dave` | 4 |
| eve@example.com | `sk-seed-eve` | 5 |

---

## Pre-populated Meetings

| Meeting ID | Status | Title | Initiator | Participants | Test Purpose |
|------------|--------|-------|-----------|-------------|--------------|
| mtg_seed_001 | COLLECTING | Sprint 14 Planning Meeting | alice | bob, carol(pending) | Plugin submit time |
| mtg_seed_002 | ANALYZING | Technical Review | dave | alice, eve(submitted) | Agent no conflict->CONFIRMED |
| mtg_seed_003 | ANALYZING | Product Req Alignment | alice | bob, carol(submitted) | Agent conflict->NEGOTIATING |
| mtg_seed_004 | CONFIRMED | Code Review Weekly | bob | alice, dave | Query completed meeting |

---

## Agent Team Test Steps

### Step 1: Poll pending coordination tasks

```
GET /api/agent/tasks/pending
```

Expected: Returns mtg_seed_002 and mtg_seed_003 as two tasks, each containing participant time slots and preferences.

### Step 2: Submit CONFIRMED for no-conflict meeting

```
POST /api/agent/meetings/mtg_seed_002/result
Content-Type: application/json

{{
  "decision_status": "CONFIRMED",
  "final_time": "2026-03-21 10:00-10:30",
  "agent_reasoning": "All three available at 10:00-11:00, selecting first 30 minutes",
  "counter_proposals": []
}}
```

Expected: Meeting status changes to CONFIRMED, final_time is set.

### Step 3: Submit NEGOTIATING for conflicting meeting

In mtg_seed_003, alice is only available in the morning, bob only in the afternoon, complete time conflict.

```
POST /api/agent/meetings/mtg_seed_003/result
Content-Type: application/json

{{
  "decision_status": "NEGOTIATING",
  "final_time": null,
  "agent_reasoning": "alice only available in the morning, bob only in the afternoon, no overlap",
  "counter_proposals": [
    {{
      "target_email": "alice@example.com",
      "message": "bob is only available in the afternoon, can you extend to 13:00?"
    }},
    {{
      "target_email": "bob@example.com",
      "message": "alice is only available in the morning, can you move up to 12:00?"
    }}
  ]
}}
```

Expected: Meeting status changes to NEGOTIATING, participants receive compromise suggestions.

### Step 4: Verify compromise suggestions delivered

```
GET /api/tasks/pending?token=sk-seed-alice
```

Expected: alice's pending tasks include task_type=COUNTER_PROPOSAL, message contains Agent's suggestion.

---

## Plugin Team Test Steps

### Step 1: Query pending tasks

```
GET /api/tasks/pending?token=sk-seed-bob
```

Expected: bob has mtg_seed_001 pending, task_type=INITIAL_SUBMIT.

### Step 2: bob submits available time

```
POST /api/meetings/mtg_seed_001/submit?token=sk-seed-bob
Content-Type: application/json

{{
  "response_type": "INITIAL",
  "available_slots": ["2026-03-20 10:00-12:00"],
  "preference_note": "morning available"
}}
```

Expected: Submission successful, status remains COLLECTING (carol hasn't submitted).

### Step 3: carol submits (triggers ANALYZING)

```
POST /api/meetings/mtg_seed_001/submit?token=sk-seed-carol
Content-Type: application/json

{{
  "response_type": "INITIAL",
  "available_slots": ["2026-03-20 14:00-16:00"],
  "preference_note": "afternoon available"
}}
```

Expected: all_submitted=true, status automatically transitions to ANALYZING.

### Step 4: Query meeting details

```
GET /api/meetings/mtg_seed_004?token=sk-seed-bob
```

Expected: Returns completed meeting details, including final_time and coordinator_reasoning.

---

## API Quick Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | /api/auth/bind | No | Email registration/binding, returns Token |
| GET | /api/meetings | Required | My meeting list |
| POST | /api/meetings | Required | Create meeting |
| GET | /api/meetings/{{id}} | Required | Query meeting details |
| POST | /api/meetings/{{id}}/submit | Required | Submit time/response |
| GET | /api/tasks/pending | Required | Plugin pending tasks |
| GET | /api/agent/tasks/pending | No | Agent poll pending coordination tasks |
| POST | /api/agent/meetings/{{id}}/result | No | Agent submit coordination result |

---

## response_type Enum Reference

| Value | Use Case | Requires available_slots |
|-------|----------|------------------------|
| INITIAL | First time submission | Yes |
| NEW_PROPOSAL | Resubmit after negotiation | Yes |
| ACCEPT_PROPOSAL | Accept Coordinator suggestion | No |
| REJECT | Reject further negotiation | No |

## decision_status Enum Reference (for Agent)

| Value | Meaning | Requires final_time |
|-------|---------|-------------------|
| CONFIRMED | Common time found | Yes |
| NEGOTIATING | Conflict, compromise needed | No |
| FAILED | Negotiation completely failed | No |
"""

    guide_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "INTEGRATION_GUIDE.md")
    with open(guide_path, "w", encoding="utf-8") as f:
        f.write(guide_content)

    print(f"  Integration guide exported to: {guide_path}")
    print("  Can be shared directly with Agent/Plugin teams")
    print()

except Exception as e:
    db.rollback()
    print(f"\n[ERROR] Seed data creation failed: {e}")
    raise
finally:
    db.close()
