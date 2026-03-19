"""
测试无冲突场景：所有参与者提交相同时间 → 直接 CONFIRMED
流程：PENDING → COLLECTING → ANALYZING → CONFIRMED
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


# ------ 连通性检查 ------
try:
    code, _ = api("GET", "/health")
    assert code == 200
except Exception:
    print("  无法连接到服务器，请先启动！")
    sys.exit(1)

# ============================================================
#  准备：注册3个用户
# ============================================================
section("1. 注册用户")

# 关键：所有人将使用完全相同的时间槽字符串
# 发起人的 available_slots 是字符串列表
# 参与者提交的 {"start":"X","end":"Y"} 会被归一化为 "X-Y"
# 所以要让两种格式归一化后完全一致

COMMON_SLOT_STR = "2026-03-20 14:00-2026-03-20 16:00"  # 发起人用这个字符串
COMMON_SLOT_DICT = {"start": "2026-03-20 14:00", "end": "2026-03-20 16:00"}  # 参与者用这个字典

_, d = api("POST", "/api/auth/bind", body={"email": "leader@demo.com"})
leader_token = d["data"]["token"]
check("注册发起人 leader", True)

_, d = api("POST", "/api/auth/bind", body={"email": "dev1@demo.com"})
dev1_token = d["data"]["token"]
check("注册参与者 dev1", True)

_, d = api("POST", "/api/auth/bind", body={"email": "dev2@demo.com"})
dev2_token = d["data"]["token"]
check("注册参与者 dev2", True)

# ============================================================
#  创建会议 → 状态应为 COLLECTING
# ============================================================
section("2. 创建会议")

code, data = api("POST", "/api/meetings", token=leader_token, body={
    "title": "无冲突测试会议",
    "duration_minutes": 60,
    "invitees": ["dev1@demo.com", "dev2@demo.com"],
    "initiator_data": {
        "available_slots": [COMMON_SLOT_STR],
        "preference_note": "下午两点最佳"
    }
})
check("创建会议成功", code == 200)
meeting_id = data["data"]["id"]
check("状态为 COLLECTING", data["data"]["status"] == "COLLECTING")
print(f"  会议ID: {meeting_id}")

# ============================================================
#  dev1 提交 — 相同时间槽
# ============================================================
section("3. dev1 提交空闲时间（与发起人相同）")

code, data = api("POST", f"/api/meetings/{meeting_id}/submit", token=dev1_token, body={
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT_DICT],
    "preference_note": "OK"
})
check("dev1 提交成功", code == 200)
check("状态仍为 COLLECTING（还差 dev2）", data["data"]["status"] == "COLLECTING")
check("all_submitted = False", data["data"]["all_submitted"] == False)

# ============================================================
#  dev2 提交 — 相同时间槽 → 触发 Coordinator → CONFIRMED
# ============================================================
section("4. dev2 提交空闲时间 → 全员提交 → 自动分析")

code, data = api("POST", f"/api/meetings/{meeting_id}/submit", token=dev2_token, body={
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT_DICT]
})
check("dev2 提交成功", code == 200,
      f"实际: {code} {json.dumps(data, ensure_ascii=False)[:300]}")

if code != 200:
    print(f"\n  [DEBUG] {json.dumps(data, indent=2, ensure_ascii=False)}")
    sys.exit(1)

check("all_submitted = True", data["data"]["all_submitted"] == True)

coordinator_result = data["data"].get("coordinator_result")
check("Coordinator 返回了分析结果", coordinator_result is not None)

final_status = data["data"]["status"]
check(f"最终状态为 CONFIRMED（实际: {final_status}）", final_status == "CONFIRMED")

if coordinator_result:
    print(f"\n  Coordinator 分析结果:")
    print(f"  {json.dumps(coordinator_result, indent=4, ensure_ascii=False)}")

# ============================================================
#  验证会议详情
# ============================================================
section("5. 查询会议详情 — 验证最终结果")

code, data = api("GET", f"/api/meetings/{meeting_id}", token=leader_token)
check("查询详情成功", code == 200)
check("状态为 CONFIRMED", data["data"]["status"] == "CONFIRMED")
check("final_time 不为空", data["data"]["final_time"] is not None)
check("所有参与者都已提交",
      all(p["has_submitted"] for p in data["data"]["participants"]))

print(f"\n  最终确认时间: {data['data']['final_time']}")
print(f"  参与者:")
for p in data["data"]["participants"]:
    print(f"    {p['email']} ({p['role']}) - 已提交: {p['has_submitted']}")

# ============================================================
#  验证已 CONFIRMED 的会议不能再提交
# ============================================================
section("6. 边界测试 — 已确认的会议不能再提交")

code, data = api("POST", f"/api/meetings/{meeting_id}/submit", token=dev1_token, body={
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT_DICT]
})
check("已 CONFIRMED 的会议提交返回 400", code == 400)

# ============================================================
#  验证待办任务已清空
# ============================================================
section("7. 验证待办任务已清空")

code, data = api("GET", "/api/tasks/pending", token=dev1_token)
# 过滤出本次会议的待办
my_tasks = [t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_id]
check("dev1 对该会议无待办任务", len(my_tasks) == 0)

# ============================================================
#  验证会议列表中的状态
# ============================================================
section("8. 验证会议列表状态")

code, data = api("GET", "/api/meetings", token=leader_token)
m = next((m for m in data["data"]["meetings"] if m["meeting_id"] == meeting_id), None)
check("会议列表中能找到该会议", m is not None)
check("列表中状态为 CONFIRMED", m["status"] == "CONFIRMED")
check("进度为 3/3", m["progress"] == "3/3")

# ============================================================
#  汇总
# ============================================================
section("测试结果汇总")
total = passed + failed
print(f"\n  总计: {total} 项")
print(f"  通过: {passed} 项")
print(f"  失败: {failed} 项")
print(f"  通过率: {passed/total*100:.0f}%\n")

if failed == 0:
    print("  ALL TESTS PASSED!")
    print("\n  完整流程验证:")
    print("  PENDING → COLLECTING → ANALYZING → CONFIRMED")
else:
    print(f"  {failed} TEST(S) FAILED")
    sys.exit(1)
