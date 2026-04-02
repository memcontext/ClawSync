"""
Meeting Coordinator API - Full Test Script
Covers all 8 API endpoints + 4 end-to-end flows

Test scenarios:
  Scenario A: No conflict -> CONFIRMED (happy path)
  Scenario B: Conflict -> NEGOTIATING -> Agent compromise -> Resubmit -> CONFIRMED
  Scenario C: REJECT -> FAILED
  Scenario D: ACCEPT_PROPOSAL flow
"""

import requests
import json
import sys

BASE_URL = "http://127.0.0.1:8000"

# ========== Utility Functions ==========

passed = 0
failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}")
        failed += 1


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def post(path, json_data=None, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{path}", json=json_data, headers=headers)
    return r.status_code, r.json()


def get(path, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{BASE_URL}{path}", headers=headers)
    return r.status_code, r.json()


# ========== 0. Connectivity Check ==========

section("0. Service Connectivity Check")
try:
    code, data = get("/health")
    check("Server health check", code == 200)
except Exception as e:
    print(f"  [FATAL] Cannot connect to server: {e}")
    print("  Please start first: python -m uvicorn app.main:app --reload")
    sys.exit(1)


# ========== 1. Authentication ==========

section("1. Authentication (POST /api/auth/bind)")

code, data = post("/api/auth/bind", {"email": "alice@example.com"})
check("Register alice", code == 200 and data["code"] == 200)
alice_token = data["data"]["token"]
alice_id = data["data"]["user_id"]

code, data = post("/api/auth/bind", {"email": "alice@example.com"})
check("Duplicate registration returns same token", data["data"]["token"] == alice_token)

code, data = post("/api/auth/bind", {"email": "bob@example.com"})
check("Register bob", code == 200)
bob_token = data["data"]["token"]

code, data = post("/api/auth/bind", {"email": "carol@example.com"})
check("Register carol", code == 200)
carol_token = data["data"]["token"]

code, data = get("/api/meetings", token="invalid-token-xxx")
check("Invalid token returns 401", code == 401)

code, data = get("/api/meetings")
check("Missing token returns 422", code == 422)
check("Unified error response format (code field)", "code" in data and data["code"] == 422)


# ==========================================================
#  Scenario A: No conflict -> CONFIRMED (happy path)
# ==========================================================

section("Scenario A: No-conflict Happy Path")
print()

# ---- 2. Create meeting ----
section("A-1. Create Meeting (POST /api/meetings)")

COMMON_SLOT = "2026-03-20 14:00-15:00"

code, data = post("/api/meetings", {
    "title": "Scenario A - No Conflict Test",
    "duration_minutes": 30,
    "invitees": ["bob@example.com", "carol@example.com"],
    "initiator_data": {
        "available_slots": [COMMON_SLOT, "2026-03-20 16:00-17:00"],
        "preference_note": "Afternoon preferred"
    }
}, token=alice_token)
check("Meeting created successfully", code == 200 and data["code"] == 200)
meeting_a = data["data"]["meeting_id"]
check("Status is COLLECTING", data["data"]["status"] == "COLLECTING")
check("Returns meeting_id", meeting_a is not None)

# ---- 3. Meeting list ----
section("A-2. Meeting List (GET /api/meetings)")

code, data = get("/api/meetings", token=alice_token)
check("alice query list successful", code == 200)
check("List contains meeting", len(data["data"]["meetings"]) >= 1)

code, data = get("/api/meetings", token=bob_token)
check("bob query list successful", code == 200)
bob_meeting = next((m for m in data["data"]["meetings"] if m["meeting_id"] == meeting_a), None)
check("bob sees the meeting", bob_meeting is not None)
check("bob role is participant", bob_meeting["my_role"] == "participant")

# ---- 4. Pending tasks ----
section("A-3. Pending Tasks (GET /api/tasks/pending)")

code, data = get("/api/tasks/pending", token=bob_token)
check("bob has pending tasks", len(data["data"]["pending_tasks"]) >= 1)
bob_task = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_a), None)
check("Task type is INITIAL_SUBMIT", bob_task["task_type"] == "INITIAL_SUBMIT")
check("Message contains invitation info", "invites" in bob_task["message"])

code, data = get("/api/tasks/pending", token=alice_token)
alice_tasks = [t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_a]
check("alice has no pending (initiator already submitted)", len(alice_tasks) == 0)

# ---- 5. Bob submits ----
section("A-4. Bob Submits Available Time (POST /api/meetings/{id}/submit)")

code, data = post(f"/api/meetings/{meeting_a}/submit", {
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT, "2026-03-21 09:00-11:00"],
    "preference_note": "Busy before 3pm"
}, token=bob_token)
check("bob submission successful", code == 200)
check("Status still COLLECTING", data["data"]["status"] == "COLLECTING")
check("all_submitted=False", data["data"]["all_submitted"] == False)

# ---- 6. Carol submits (last person) -> auto transition to ANALYZING ----
section("A-5. Carol Submits -> All Submitted -> ANALYZING")

code, data = post(f"/api/meetings/{meeting_a}/submit", {
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT],
    "preference_note": "Only this time works"
}, token=carol_token)
check("carol submission successful", code == 200 and data["code"] == 200)
check("all_submitted=True", data["data"]["all_submitted"] == True)
check("Status transitions to ANALYZING", data["data"]["status"] == "ANALYZING")

# ---- 7. Agent polls pending coordination tasks ----
section("A-6. Agent Polling (GET /api/agent/tasks/pending)")

code, data = get("/api/agent/tasks/pending")
check("Agent polling successful", code == 200)
check("Has pending coordination tasks", len(data["data"]["pending_tasks"]) >= 1)

agent_task = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_a), None)
check("Found scenario A meeting", agent_task is not None)
check("Contains participants_data", len(agent_task["participants_data"]) == 3)
check("Contains duration_minutes", agent_task["duration_minutes"] == 30)

# Verify slots converted to {start, end} format
first_participant = agent_task["participants_data"][0]
check("Slots in dict format", isinstance(first_participant["latest_slots"][0], dict))
check("Slots contain start field", "start" in first_participant["latest_slots"][0])
check("Slots contain end field", "end" in first_participant["latest_slots"][0])

# ---- 8. Agent submits CONFIRMED result ----
section("A-7. Agent Submits Result (POST /api/agent/meetings/{id}/result) -> CONFIRMED")

code, data = post(f"/api/agent/meetings/{meeting_a}/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-20 14:00-14:30",
    "agent_reasoning": "All participants available at 14:00-15:00, selecting first 30 minutes as meeting time.",
    "counter_proposals": []
})
check("Agent submission successful", code == 200 and data["code"] == 200)
check("New status is CONFIRMED", data["data"]["new_status"] == "CONFIRMED")

# ---- 9. Query final result ----
section("A-8. Query Meeting Details (GET /api/meetings/{id})")

code, data = get(f"/api/meetings/{meeting_a}", token=alice_token)
check("Query successful", code == 200)
check("Status CONFIRMED", data["data"]["status"] == "CONFIRMED")
check("final_time correct", data["data"]["final_time"] == "2026-03-20 14:00-14:30")
check("coordinator_reasoning has value", data["data"]["coordinator_reasoning"] is not None)
check("Returns participants list", len(data["data"]["participants"]) == 3)

# Agent polling should have no pending tasks now
code, data = get("/api/agent/tasks/pending")
agent_tasks_a = [t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_a]
check("No pending Agent tasks after CONFIRMED", len(agent_tasks_a) == 0)


# ==========================================================
#  Scenario B: Conflict -> NEGOTIATING -> Resubmit -> CONFIRMED
# ==========================================================

section("Scenario B: Conflict Negotiation Path")
print()

# ---- Create meeting (conflicting times) ----
section("B-1. Create Conflicting Meeting")

code, data = post("/api/meetings", {
    "title": "Scenario B - Conflict Test",
    "duration_minutes": 30,
    "invitees": ["bob@example.com"],
    "initiator_data": {
        "available_slots": ["2026-03-22 09:00-12:00"],
        "preference_note": "Only available in the morning"
    }
}, token=alice_token)
check("Creation successful", code == 200)
meeting_b = data["data"]["meeting_id"]

# ---- Bob submits different time ----
section("B-2. Bob Submits Different Time -> ANALYZING")

code, data = post(f"/api/meetings/{meeting_b}/submit", {
    "response_type": "INITIAL",
    "available_slots": ["2026-03-22 14:00-18:00"],
    "preference_note": "Have class in the morning, only afternoon works"
}, token=bob_token)
check("bob submission successful", code == 200)
check("Status transitions to ANALYZING", data["data"]["status"] == "ANALYZING")

# ---- Agent polling ----
section("B-3. Agent Polls and Submits NEGOTIATING Result")

code, data = get("/api/agent/tasks/pending")
agent_task_b = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_b), None)
check("Agent found conflicting meeting", agent_task_b is not None)

# Agent submits NEGOTIATING + counter_proposals
code, data = post(f"/api/agent/meetings/{meeting_b}/result", {
    "decision_status": "NEGOTIATING",
    "final_time": None,
    "agent_reasoning": "Alice only available in the morning, Bob only in the afternoon, no overlap.",
    "counter_proposals": [
        {
            "target_email": "alice@example.com",
            "message": "Bob is only available in the afternoon, can you extend to 13:00-14:00?"
        }
    ]
})
check("Agent NEGOTIATING submission successful", code == 200)
check("New status is NEGOTIATING", data["data"]["new_status"] == "NEGOTIATING")

# ---- Plugin polls pending -> sees COUNTER_PROPOSAL ----
section("B-4. Plugin Polls and Sees Compromise Suggestion")

code, data = get("/api/tasks/pending", token=alice_token)
alice_task = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_b), None)
check("alice has pending task", alice_task is not None)
check("Task type is COUNTER_PROPOSAL", alice_task["task_type"] == "COUNTER_PROPOSAL")
check("Message contains Agent's targeted suggestion", "Bob" in alice_task["message"])

code, data = get("/api/tasks/pending", token=bob_token)
bob_task = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_b), None)
check("bob also has pending task", bob_task is not None)

# ---- Both resubmit -> ANALYZING ----
section("B-5. Both Resubmit New Times -> ANALYZING")

NEW_COMMON = "2026-03-22 13:00-14:00"

code, data = post(f"/api/meetings/{meeting_b}/submit", {
    "response_type": "NEW_PROPOSAL",
    "available_slots": ["2026-03-22 09:00-12:00", NEW_COMMON],
    "preference_note": "Can extend to 1pm"
}, token=alice_token)
check("alice resubmission successful", code == 200)
check("Still NEGOTIATING after alice submits", data["data"]["status"] == "NEGOTIATING")

code, data = post(f"/api/meetings/{meeting_b}/submit", {
    "response_type": "NEW_PROPOSAL",
    "available_slots": [NEW_COMMON, "2026-03-22 14:00-18:00"],
    "preference_note": "1pm works"
}, token=bob_token)
check("bob resubmission successful", code == 200)
check("Transitions to ANALYZING after all submit", data["data"]["status"] == "ANALYZING")

# ---- Agent re-analyzes -> CONFIRMED ----
section("B-6. Agent Re-analyzes -> CONFIRMED")

code, data = post(f"/api/agent/meetings/{meeting_b}/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-22 13:00-13:30",
    "agent_reasoning": "Both agreed on 13:00-14:00 time slot, selecting first 30 minutes.",
    "counter_proposals": []
})
check("Agent submits CONFIRMED", code == 200)
check("Final status CONFIRMED", data["data"]["new_status"] == "CONFIRMED")

code, data = get(f"/api/meetings/{meeting_b}", token=alice_token)
check("final_time correct", data["data"]["final_time"] == "2026-03-22 13:00-13:30")
check("round_count = 1", data["data"]["round_count"] == 1)


# ==========================================================
#  Scenario C: REJECT -> FAILED
# ==========================================================

section("Scenario C: Reject Negotiation -> FAILED")
print()

section("C-1. Create Meeting and Collect Times")

code, data = post("/api/meetings", {
    "title": "Scenario C - Rejection Test",
    "duration_minutes": 60,
    "invitees": ["bob@example.com"],
    "initiator_data": {
        "available_slots": ["2026-03-25 10:00-12:00"],
        "preference_note": "Tuesday morning"
    }
}, token=alice_token)
meeting_c = data["data"]["meeting_id"]

# Bob submits different time
code, data = post(f"/api/meetings/{meeting_c}/submit", {
    "response_type": "INITIAL",
    "available_slots": ["2026-03-25 16:00-18:00"],
    "preference_note": "Only available in the afternoon"
}, token=bob_token)
check("bob submits -> ANALYZING", data["data"]["status"] == "ANALYZING")

# Agent submits NEGOTIATING
code, data = post(f"/api/agent/meetings/{meeting_c}/result", {
    "decision_status": "NEGOTIATING",
    "final_time": None,
    "agent_reasoning": "Times have no overlap",
    "counter_proposals": [
        {"target_email": "bob@example.com", "message": "Alice is only available in the morning, can you adjust?"}
    ]
})
check("Agent submits NEGOTIATING", data["data"]["new_status"] == "NEGOTIATING")

# ---- Bob rejects ----
section("C-2. Bob Rejects Proposal -> FAILED")

code, data = post(f"/api/meetings/{meeting_c}/submit", {
    "response_type": "REJECT",
    "preference_note": "I really cannot adjust this time"
}, token=bob_token)
check("bob rejection successful", code == 200)
check("Status becomes FAILED", data["data"]["status"] == "FAILED")

code, data = get(f"/api/meetings/{meeting_c}", token=alice_token)
check("Query confirms FAILED", data["data"]["status"] == "FAILED")
check("Reasoning records rejection", "reject" in data["data"]["coordinator_reasoning"].lower() or "failed" in data["data"]["coordinator_reasoning"].lower())


# ==========================================================
#  Scenario D: ACCEPT_PROPOSAL flow
# ==========================================================

section("Scenario D: ACCEPT_PROPOSAL Flow")
print()

section("D-1. Create Meeting -> Conflict -> Agent NEGOTIATING")

code, data = post("/api/meetings", {
    "title": "Scenario D - Accept Proposal Test",
    "duration_minutes": 30,
    "invitees": ["bob@example.com"],
    "initiator_data": {
        "available_slots": ["2026-03-26 09:00-12:00"],
        "preference_note": "morning"
    }
}, token=alice_token)
meeting_d = data["data"]["meeting_id"]

code, data = post(f"/api/meetings/{meeting_d}/submit", {
    "response_type": "INITIAL",
    "available_slots": ["2026-03-26 14:00-17:00"],
}, token=bob_token)

code, data = post(f"/api/agent/meetings/{meeting_d}/result", {
    "decision_status": "NEGOTIATING",
    "final_time": None,
    "agent_reasoning": "No overlap",
    "counter_proposals": [
        {"target_email": "alice@example.com", "message": "Suggest moving to afternoon"},
        {"target_email": "bob@example.com", "message": "Suggest moving to morning"}
    ]
})
check("Enters NEGOTIATING", data["data"]["new_status"] == "NEGOTIATING")

section("D-2. Both ACCEPT_PROPOSAL -> ANALYZING")

code, data = post(f"/api/meetings/{meeting_d}/submit", {
    "response_type": "ACCEPT_PROPOSAL"
}, token=alice_token)
check("alice accepts, waiting for bob", data["data"]["all_submitted"] == False)

code, data = post(f"/api/meetings/{meeting_d}/submit", {
    "response_type": "ACCEPT_PROPOSAL"
}, token=bob_token)
check("bob accepts, all completed", data["data"]["all_submitted"] == True)
check("Status transitions to ANALYZING", data["data"]["status"] == "ANALYZING")

# Agent final confirmation
code, data = post(f"/api/agent/meetings/{meeting_d}/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-26 11:00-11:30",
    "agent_reasoning": "Both accepted compromise proposal",
    "counter_proposals": []
})
check("Final CONFIRMED", data["data"]["new_status"] == "CONFIRMED")


# ==========================================================
#  Error Handling Tests
# ==========================================================

section("Error Handling Tests")

# Submit to already CONFIRMED meeting
code, data = post(f"/api/meetings/{meeting_a}/submit", {
    "response_type": "INITIAL",
    "available_slots": ["2026-03-20 14:00-15:00"]
}, token=bob_token)
check("CONFIRMED meeting rejects submission (400)", code == 400)
check("Unified error format", data["code"] == 400)

# Submit to non-existent meeting
code, data = post("/api/agent/meetings/mtg_nonexist/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-20 14:00-14:30",
    "agent_reasoning": "test",
    "counter_proposals": []
})
check("Non-existent meeting returns 404", code == 404)

# Submit Agent result to non-ANALYZING meeting
code, data = post(f"/api/agent/meetings/{meeting_a}/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-20 14:00-14:30",
    "agent_reasoning": "test",
    "counter_proposals": []
})
check("Non-ANALYZING status rejects Agent submission (400)", code == 400)

# Invalid response_type
code, data = post(f"/api/meetings/{meeting_a}/submit", {
    "response_type": "INVALID_TYPE",
    "available_slots": []
}, token=bob_token)
check("Invalid response_type returns 422", code == 422)


# ========== Final Report ==========

print(f"\n{'=' * 60}")
print(f"  Test completed: {passed} passed / {failed} failed / {passed + failed} total")
print(f"{'=' * 60}")

if failed > 0:
    sys.exit(1)
