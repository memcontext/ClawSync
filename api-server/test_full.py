import requests
import json
import time

BASE_URL = "http://localhost:8000"


def test_full_flow():
    print("=" * 60)
    print("Full Flow Test")
    print("=" * 60)

    # 1. Get tokens
    print("\n1. Getting user tokens...")

    resp = requests.post(f"{BASE_URL}/api/auth/bind", json={"email": "initiator@test.com"})
    init_token = resp.json()["data"]["token"]

    resp = requests.post(f"{BASE_URL}/api/auth/bind", json={"email": "user1@test.com"})
    user1_token = resp.json()["data"]["token"]

    resp = requests.post(f"{BASE_URL}/api/auth/bind", json={"email": "user2@test.com"})
    user2_token = resp.json()["data"]["token"]

    # 2. Create meeting
    print("\n2. Creating meeting...")
    meeting_data = {
        "title": "Full Flow Test Meeting",
        "duration_minutes": 30,
        "invitees": ["user1@test.com", "user2@test.com"],
        "initiator_data": {
            "available_slots": ["2026-03-18 14:00-18:00", "2026-03-19 10:00-12:00"]
        }
    }

    resp = requests.post(
        f"{BASE_URL}/api/meetings",
        json=meeting_data,
        headers={"Authorization": f"Bearer {init_token}"}
    )
    meeting_id = resp.json()["data"]["id"]
    print(f"Meeting ID: {meeting_id}")

    # 3. user1 submits time
    print("\n3. user1 submitting time...")
    slots1 = {
        "response_type": "INITIAL",
        "available_slots": [
            {"start": "2026-03-18 15:00", "end": "2026-03-18 17:00"}
        ]
    }
    resp = requests.post(
        f"{BASE_URL}/api/meetings/{meeting_id}/submit",
        json=slots1,
        headers={"Authorization": f"Bearer {user1_token}"}
    )
    print(f"user1 submission result: {resp.status_code}")

    # 4. user2 submits time
    print("\n4. user2 submitting time...")
    slots2 = {
        "response_type": "INITIAL",
        "available_slots": [
            {"start": "2026-03-18 16:00", "end": "2026-03-18 18:00"}
        ]
    }
    resp = requests.post(
        f"{BASE_URL}/api/meetings/{meeting_id}/submit",
        json=slots2,
        headers={"Authorization": f"Bearer {user2_token}"}
    )
    print(f"user2 submission result: {resp.status_code}")

    # 5. Check meeting status
    print("\n5. Final meeting status...")
    time.sleep(1)
    resp = requests.get(
        f"{BASE_URL}/api/meetings/{meeting_id}",
        headers={"Authorization": f"Bearer {init_token}"}
    )
    data = resp.json()
    print(f"Meeting status: {data['data']['status']}")

    print("\n" + "=" * 60)
    print("Test completed!")
    print("=" * 60)


if __name__ == "__main__":
    test_full_flow()
