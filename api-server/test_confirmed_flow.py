"""
Test no-conflict scenario: all participants submit the same time -> directly CONFIRMED
Flow: PENDING -> COLLECTING -> ANALYZING -> CONFIRMED
"""

import requests
import json
import sys

BASE_URL = "http://localhost:8000"

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def api(method, path, token=None, body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = f"{BASE_URL}{path}"
    r = requests.post(url, json=body, headers=headers) if method == "POST" else requests.get(url, headers=headers)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}


def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


# ------ Connectivity check ------
try:
    code, _ = api("GET", "/health")
    assert code == 200
except Exception:
    print("  Cannot connect to server, please start it first!")
    sys.exit(1)

# ============================================================
#  Preparation: Register 3 users
# ============================================================
section("1. Register users")

# Key: all will use the exact same time slot string
# Initiator's available_slots is a string list
# Participants submit {"start":"X","end":"Y"} which gets normalized to "X-Y"
# So both formats must be identical after normalization

COMMON_SLOT_STR = "2026-03-20 14:00-2026-03-20 16:00"  # Initiator uses this string
COMMON_SLOT_DICT = {"start": "2026-03-20 14:00", "end": "2026-03-20 16:00"}  # Participants use this dict

_, d = api("POST", "/api/auth/bind", body={"email": "leader@demo.com"})
leader_token = d["data"]["token"]
check("Register initiator leader", True)

_, d = api("POST", "/api/auth/bind", body={"email": "dev1@demo.com"})
dev1_token = d["data"]["token"]
check("Register participant dev1", True)

_, d = api("POST", "/api/auth/bind", body={"email": "dev2@demo.com"})
dev2_token = d["data"]["token"]
check("Register participant dev2", True)

# ============================================================
#  Create meeting -> status should be COLLECTING
# ============================================================
section("2. Create meeting")

code, data = api("POST", "/api/meetings", token=leader_token, body={
    "title": "No-conflict test meeting",
    "duration_minutes": 60,
    "invitees": ["dev1@demo.com", "dev2@demo.com"],
    "initiator_data": {
        "available_slots": [COMMON_SLOT_STR],
        "preference_note": "2pm is best"
    }
})
check("Meeting created successfully", code == 200)
meeting_id = data["data"]["id"]
check("Status is COLLECTING", data["data"]["status"] == "COLLECTING")
print(f"  Meeting ID: {meeting_id}")

# ============================================================
#  dev1 submits -- same time slot
# ============================================================
section("3. dev1 submits available time (same as initiator)")

code, data = api("POST", f"/api/meetings/{meeting_id}/submit", token=dev1_token, body={
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT_DICT],
    "preference_note": "OK"
})
check("dev1 submission successful", code == 200)
check("Status still COLLECTING (waiting for dev2)", data["data"]["status"] == "COLLECTING")
check("all_submitted = False", data["data"]["all_submitted"] == False)

# ============================================================
#  dev2 submits -- same time slot -> triggers Coordinator -> CONFIRMED
# ============================================================
section("4. dev2 submits available time -> all submitted -> auto analysis")

code, data = api("POST", f"/api/meetings/{meeting_id}/submit", token=dev2_token, body={
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT_DICT]
})
check("dev2 submission successful", code == 200,
      f"actual: {code} {json.dumps(data, ensure_ascii=False)[:300]}")

if code != 200:
    print(f"\n  [DEBUG] {json.dumps(data, indent=2, ensure_ascii=False)}")
    sys.exit(1)

check("all_submitted = True", data["data"]["all_submitted"] == True)

coordinator_result = data["data"].get("coordinator_result")
check("Coordinator returned analysis result", coordinator_result is not None)

final_status = data["data"]["status"]
check(f"Final status is CONFIRMED (actual: {final_status})", final_status == "CONFIRMED")

if coordinator_result:
    print(f"\n  Coordinator analysis result:")
    print(f"  {json.dumps(coordinator_result, indent=4, ensure_ascii=False)}")

# ============================================================
#  Verify meeting details
# ============================================================
section("5. Query meeting details -- verify final result")

code, data = api("GET", f"/api/meetings/{meeting_id}", token=leader_token)
check("Query details successful", code == 200)
check("Status is CONFIRMED", data["data"]["status"] == "CONFIRMED")
check("final_time is not empty", data["data"]["final_time"] is not None)
check("All participants have submitted",
      all(p["has_submitted"] for p in data["data"]["participants"]))

print(f"\n  Final confirmed time: {data['data']['final_time']}")
print(f"  Participants:")
for p in data["data"]["participants"]:
    print(f"    {p['email']} ({p['role']}) - submitted: {p['has_submitted']}")

# ============================================================
#  Verify CONFIRMED meeting cannot be submitted again
# ============================================================
section("6. Boundary test -- confirmed meeting cannot be submitted again")

code, data = api("POST", f"/api/meetings/{meeting_id}/submit", token=dev1_token, body={
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT_DICT]
})
check("CONFIRMED meeting submission returns 400", code == 400)

# ============================================================
#  Verify pending tasks cleared
# ============================================================
section("7. Verify pending tasks cleared")

code, data = api("GET", "/api/tasks/pending", token=dev1_token)
# Filter pending tasks for this meeting
my_tasks = [t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_id]
check("dev1 has no pending tasks for this meeting", len(my_tasks) == 0)

# ============================================================
#  Verify meeting list status
# ============================================================
section("8. Verify meeting list status")

code, data = api("GET", "/api/meetings", token=leader_token)
m = next((m for m in data["data"]["meetings"] if m["meeting_id"] == meeting_id), None)
check("Meeting found in list", m is not None)
check("Status in list is CONFIRMED", m["status"] == "CONFIRMED")
check("Progress is 3/3", m["progress"] == "3/3")

# ============================================================
#  Summary
# ============================================================
section("Test Results Summary")
total = passed + failed
print(f"\n  Total: {total} items")
print(f"  Passed: {passed} items")
print(f"  Failed: {failed} items")
print(f"  Pass rate: {passed/total*100:.0f}%\n")

if failed == 0:
    print("  ALL TESTS PASSED!")
    print("\n  Full flow verified:")
    print("  PENDING -> COLLECTING -> ANALYZING -> CONFIRMED")
else:
    print(f"  {failed} TEST(S) FAILED")
    sys.exit(1)
