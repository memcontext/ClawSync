import requests
import json
import time

BASE_URL = "http://localhost:8000"


def test_full_flow():
    print("=" * 60)
    print("完整流程测试")
    print("=" * 60)

    # 1. 获取tokens
    print("\n1. 获取用户tokens...")

    resp = requests.post(f"{BASE_URL}/api/auth/bind", json={"email": "initiator@test.com"})
    init_token = resp.json()["data"]["token"]

    resp = requests.post(f"{BASE_URL}/api/auth/bind", json={"email": "user1@test.com"})
    user1_token = resp.json()["data"]["token"]

    resp = requests.post(f"{BASE_URL}/api/auth/bind", json={"email": "user2@test.com"})
    user2_token = resp.json()["data"]["token"]

    # 2. 创建会议
    print("\n2. 创建会议...")
    meeting_data = {
        "title": "完整流程测试会议",
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
    print(f"会议ID: {meeting_id}")

    # 3. user1提交时间
    print("\n3. user1提交时间...")
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
    print(f"user1提交结果: {resp.status_code}")

    # 4. user2提交时间
    print("\n4. user2提交时间...")
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
    print(f"user2提交结果: {resp.status_code}")

    # 5. 检查会议状态
    print("\n5. 最终会议状态...")
    time.sleep(1)
    resp = requests.get(
        f"{BASE_URL}/api/meetings/{meeting_id}",
        headers={"Authorization": f"Bearer {init_token}"}
    )
    data = resp.json()
    print(f"会议状态: {data['data']['status']}")

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    test_full_flow()