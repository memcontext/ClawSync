"""
Meeting Coordinator API - 完整测试脚本
覆盖全部 8 个 API 接口 + 4 种端到端流程

测试场景:
  场景 A: 无冲突 → CONFIRMED（快乐路径）
  场景 B: 有冲突 → NEGOTIATING → Agent 妥协 → 重新提交 → CONFIRMED
  场景 C: REJECT → FAILED
  场景 D: ACCEPT_PROPOSAL 流程
"""

import requests
import json
import sys

BASE_URL = "http://127.0.0.1:8000"

# ========== 工具函数 ==========

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


# ========== 0. 连通性检查 ==========

section("0. 服务连通性检查")
try:
    code, data = get("/health")
    check("服务器健康检查", code == 200)
except Exception as e:
    print(f"  [FATAL] 无法连接服务器: {e}")
    print("  请先启动: python -m uvicorn app.main:app --reload")
    sys.exit(1)


# ========== 1. 身份认证 ==========

section("1. 身份认证 (POST /api/auth/bind)")

code, data = post("/api/auth/bind", {"email": "alice@example.com"})
check("注册 alice", code == 200 and data["code"] == 200)
alice_token = data["data"]["token"]
alice_id = data["data"]["user_id"]

code, data = post("/api/auth/bind", {"email": "alice@example.com"})
check("重复注册返回相同 token", data["data"]["token"] == alice_token)

code, data = post("/api/auth/bind", {"email": "bob@example.com"})
check("注册 bob", code == 200)
bob_token = data["data"]["token"]

code, data = post("/api/auth/bind", {"email": "carol@example.com"})
check("注册 carol", code == 200)
carol_token = data["data"]["token"]

code, data = get("/api/meetings", token="invalid-token-xxx")
check("无效 Token 返回 401", code == 401)

code, data = get("/api/meetings")
check("缺少 Token 返回 422", code == 422)
check("错误响应格式统一 (code 字段)", "code" in data and data["code"] == 422)


# ==========================================================
#  场景 A: 无冲突 → CONFIRMED（快乐路径）
# ==========================================================

section("场景 A: 无冲突快乐路径")
print()

# ---- 2. 创建会议 ----
section("A-1. 创建会议 (POST /api/meetings)")

COMMON_SLOT = "2026-03-20 14:00-15:00"

code, data = post("/api/meetings", {
    "title": "场景A-无冲突测试",
    "duration_minutes": 30,
    "invitees": ["bob@example.com", "carol@example.com"],
    "initiator_data": {
        "available_slots": [COMMON_SLOT, "2026-03-20 16:00-17:00"],
        "preference_note": "下午优先"
    }
}, token=alice_token)
check("创建会议成功", code == 200 and data["code"] == 200)
meeting_a = data["data"]["meeting_id"]
check("状态为 COLLECTING", data["data"]["status"] == "COLLECTING")
check("返回 meeting_id", meeting_a is not None)

# ---- 3. 会议列表 ----
section("A-2. 会议列表 (GET /api/meetings)")

code, data = get("/api/meetings", token=alice_token)
check("alice 查询列表成功", code == 200)
check("列表包含会议", len(data["data"]["meetings"]) >= 1)

code, data = get("/api/meetings", token=bob_token)
check("bob 查询列表成功", code == 200)
bob_meeting = next((m for m in data["data"]["meetings"] if m["meeting_id"] == meeting_a), None)
check("bob 看到会议", bob_meeting is not None)
check("bob 角色为 participant", bob_meeting["my_role"] == "participant")

# ---- 4. 待办任务 ----
section("A-3. 待办任务 (GET /api/tasks/pending)")

code, data = get("/api/tasks/pending", token=bob_token)
check("bob 有待办任务", len(data["data"]["pending_tasks"]) >= 1)
bob_task = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_a), None)
check("任务类型为 INITIAL_SUBMIT", bob_task["task_type"] == "INITIAL_SUBMIT")
check("message 包含邀请信息", "邀请" in bob_task["message"])

code, data = get("/api/tasks/pending", token=alice_token)
alice_tasks = [t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_a]
check("alice 无待办（发起人已提交）", len(alice_tasks) == 0)

# ---- 5. Bob 提交 ----
section("A-4. Bob 提交空闲时间 (POST /api/meetings/{id}/submit)")

code, data = post(f"/api/meetings/{meeting_a}/submit", {
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT, "2026-03-21 09:00-11:00"],
    "preference_note": "我3点之前有事"
}, token=bob_token)
check("bob 提交成功", code == 200)
check("状态仍为 COLLECTING", data["data"]["status"] == "COLLECTING")
check("all_submitted=False", data["data"]["all_submitted"] == False)

# ---- 6. Carol 提交（最后一人）→ 自动转 ANALYZING ----
section("A-5. Carol 提交 → 全员提交 → ANALYZING")

code, data = post(f"/api/meetings/{meeting_a}/submit", {
    "response_type": "INITIAL",
    "available_slots": [COMMON_SLOT],
    "preference_note": "只有这个时间可以"
}, token=carol_token)
check("carol 提交成功", code == 200 and data["code"] == 200)
check("all_submitted=True", data["data"]["all_submitted"] == True)
check("状态转为 ANALYZING", data["data"]["status"] == "ANALYZING")

# ---- 7. Agent 轮询待协调任务 ----
section("A-6. Agent 轮询 (GET /api/agent/tasks/pending)")

code, data = get("/api/agent/tasks/pending")
check("Agent 轮询成功", code == 200)
check("有待协调任务", len(data["data"]["pending_tasks"]) >= 1)

agent_task = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_a), None)
check("找到场景A的会议", agent_task is not None)
check("包含 participants_data", len(agent_task["participants_data"]) == 3)
check("包含 duration_minutes", agent_task["duration_minutes"] == 30)

# 验证 slots 转为 {start, end} 格式
first_participant = agent_task["participants_data"][0]
check("slots 为字典格式", isinstance(first_participant["latest_slots"][0], dict))
check("slots 包含 start 字段", "start" in first_participant["latest_slots"][0])
check("slots 包含 end 字段", "end" in first_participant["latest_slots"][0])

# ---- 8. Agent 提交 CONFIRMED 结果 ----
section("A-7. Agent 提交结果 (POST /api/agent/meetings/{id}/result) → CONFIRMED")

code, data = post(f"/api/agent/meetings/{meeting_a}/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-20 14:00-14:30",
    "agent_reasoning": "所有参与者在 14:00-15:00 都有空，选取前 30 分钟作为会议时间。",
    "counter_proposals": []
})
check("Agent 提交成功", code == 200 and data["code"] == 200)
check("新状态为 CONFIRMED", data["data"]["new_status"] == "CONFIRMED")

# ---- 9. 查询最终结果 ----
section("A-8. 查询会议详情 (GET /api/meetings/{id})")

code, data = get(f"/api/meetings/{meeting_a}", token=alice_token)
check("查询成功", code == 200)
check("状态 CONFIRMED", data["data"]["status"] == "CONFIRMED")
check("final_time 正确", data["data"]["final_time"] == "2026-03-20 14:00-14:30")
check("coordinator_reasoning 有值", data["data"]["coordinator_reasoning"] is not None)
check("返回 participants 列表", len(data["data"]["participants"]) == 3)

# Agent 轮询应该没有待处理任务了
code, data = get("/api/agent/tasks/pending")
agent_tasks_a = [t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_a]
check("CONFIRMED 后 Agent 无待处理任务", len(agent_tasks_a) == 0)


# ==========================================================
#  场景 B: 有冲突 → NEGOTIATING → 重新提交 → CONFIRMED
# ==========================================================

section("场景 B: 冲突协商路径")
print()

# ---- 创建会议（时间有冲突） ----
section("B-1. 创建冲突会议")

code, data = post("/api/meetings", {
    "title": "场景B-冲突测试",
    "duration_minutes": 30,
    "invitees": ["bob@example.com"],
    "initiator_data": {
        "available_slots": ["2026-03-22 09:00-12:00"],
        "preference_note": "只有上午有空"
    }
}, token=alice_token)
check("创建成功", code == 200)
meeting_b = data["data"]["meeting_id"]

# ---- Bob 提交不同时间 ----
section("B-2. Bob 提交不同时间 → ANALYZING")

code, data = post(f"/api/meetings/{meeting_b}/submit", {
    "response_type": "INITIAL",
    "available_slots": ["2026-03-22 14:00-18:00"],
    "preference_note": "上午有课，只有下午可以"
}, token=bob_token)
check("bob 提交成功", code == 200)
check("状态转为 ANALYZING", data["data"]["status"] == "ANALYZING")

# ---- Agent 轮询 ----
section("B-3. Agent 轮询并提交 NEGOTIATING 结果")

code, data = get("/api/agent/tasks/pending")
agent_task_b = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_b), None)
check("Agent 找到冲突会议", agent_task_b is not None)

# Agent 提交 NEGOTIATING + counter_proposals
code, data = post(f"/api/agent/meetings/{meeting_b}/result", {
    "decision_status": "NEGOTIATING",
    "final_time": None,
    "agent_reasoning": "Alice 只有上午有空，Bob 只有下午有空，完全没有交集。",
    "counter_proposals": [
        {
            "target_email": "alice@example.com",
            "message": "Bob 只有下午才有空，您能否将时间延长到 13:00-14:00？"
        }
    ]
})
check("Agent 提交 NEGOTIATING 成功", code == 200)
check("新状态为 NEGOTIATING", data["data"]["new_status"] == "NEGOTIATING")

# ---- Plugin 轮询待办 → 看到 COUNTER_PROPOSAL ----
section("B-4. Plugin 轮询看到妥协建议")

code, data = get("/api/tasks/pending", token=alice_token)
alice_task = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_b), None)
check("alice 有待办任务", alice_task is not None)
check("任务类型为 COUNTER_PROPOSAL", alice_task["task_type"] == "COUNTER_PROPOSAL")
check("message 包含 Agent 定向建议", "Bob" in alice_task["message"])

code, data = get("/api/tasks/pending", token=bob_token)
bob_task = next((t for t in data["data"]["pending_tasks"] if t["meeting_id"] == meeting_b), None)
check("bob 也有待办任务", bob_task is not None)

# ---- 双方重新提交 → ANALYZING ----
section("B-5. 双方重新提交新时间 → ANALYZING")

NEW_COMMON = "2026-03-22 13:00-14:00"

code, data = post(f"/api/meetings/{meeting_b}/submit", {
    "response_type": "NEW_PROPOSAL",
    "available_slots": ["2026-03-22 09:00-12:00", NEW_COMMON],
    "preference_note": "可以延长到下午1点"
}, token=alice_token)
check("alice 重新提交成功", code == 200)
check("alice 提交后仍在 NEGOTIATING", data["data"]["status"] == "NEGOTIATING")

code, data = post(f"/api/meetings/{meeting_b}/submit", {
    "response_type": "NEW_PROPOSAL",
    "available_slots": [NEW_COMMON, "2026-03-22 14:00-18:00"],
    "preference_note": "13点可以"
}, token=bob_token)
check("bob 重新提交成功", code == 200)
check("全员提交后转 ANALYZING", data["data"]["status"] == "ANALYZING")

# ---- Agent 再次分析 → CONFIRMED ----
section("B-6. Agent 再次分析 → CONFIRMED")

code, data = post(f"/api/agent/meetings/{meeting_b}/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-22 13:00-13:30",
    "agent_reasoning": "双方都同意 13:00-14:00 时间段，选取前 30 分钟。",
    "counter_proposals": []
})
check("Agent 提交 CONFIRMED", code == 200)
check("最终状态 CONFIRMED", data["data"]["new_status"] == "CONFIRMED")

code, data = get(f"/api/meetings/{meeting_b}", token=alice_token)
check("final_time 正确", data["data"]["final_time"] == "2026-03-22 13:00-13:30")
check("round_count = 1", data["data"]["round_count"] == 1)


# ==========================================================
#  场景 C: REJECT → FAILED
# ==========================================================

section("场景 C: 拒绝协商 → FAILED")
print()

section("C-1. 创建会议并收集时间")

code, data = post("/api/meetings", {
    "title": "场景C-拒绝测试",
    "duration_minutes": 60,
    "invitees": ["bob@example.com"],
    "initiator_data": {
        "available_slots": ["2026-03-25 10:00-12:00"],
        "preference_note": "周二上午"
    }
}, token=alice_token)
meeting_c = data["data"]["meeting_id"]

# Bob 提交不同时间
code, data = post(f"/api/meetings/{meeting_c}/submit", {
    "response_type": "INITIAL",
    "available_slots": ["2026-03-25 16:00-18:00"],
    "preference_note": "只有下午有空"
}, token=bob_token)
check("bob 提交 → ANALYZING", data["data"]["status"] == "ANALYZING")

# Agent 提交 NEGOTIATING
code, data = post(f"/api/agent/meetings/{meeting_c}/result", {
    "decision_status": "NEGOTIATING",
    "final_time": None,
    "agent_reasoning": "时间完全不重叠",
    "counter_proposals": [
        {"target_email": "bob@example.com", "message": "Alice 只有上午有空，您能调整吗？"}
    ]
})
check("Agent 提交 NEGOTIATING", data["data"]["new_status"] == "NEGOTIATING")

# ---- Bob 拒绝 ----
section("C-2. Bob 拒绝方案 → FAILED")

code, data = post(f"/api/meetings/{meeting_c}/submit", {
    "response_type": "REJECT",
    "preference_note": "这个时间我确实没法调整"
}, token=bob_token)
check("bob 拒绝成功", code == 200)
check("状态变为 FAILED", data["data"]["status"] == "FAILED")

code, data = get(f"/api/meetings/{meeting_c}", token=alice_token)
check("查询确认 FAILED", data["data"]["status"] == "FAILED")
check("reasoning 记录拒绝原因", "拒绝" in data["data"]["coordinator_reasoning"])


# ==========================================================
#  场景 D: ACCEPT_PROPOSAL 流程
# ==========================================================

section("场景 D: ACCEPT_PROPOSAL 流程")
print()

section("D-1. 创建会议 → 冲突 → Agent NEGOTIATING")

code, data = post("/api/meetings", {
    "title": "场景D-接受方案测试",
    "duration_minutes": 30,
    "invitees": ["bob@example.com"],
    "initiator_data": {
        "available_slots": ["2026-03-26 09:00-12:00"],
        "preference_note": "上午"
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
    "agent_reasoning": "无交集",
    "counter_proposals": [
        {"target_email": "alice@example.com", "message": "建议改到下午"},
        {"target_email": "bob@example.com", "message": "建议改到上午"}
    ]
})
check("进入 NEGOTIATING", data["data"]["new_status"] == "NEGOTIATING")

section("D-2. 双方 ACCEPT_PROPOSAL → ANALYZING")

code, data = post(f"/api/meetings/{meeting_d}/submit", {
    "response_type": "ACCEPT_PROPOSAL"
}, token=alice_token)
check("alice 接受，等待 bob", data["data"]["all_submitted"] == False)

code, data = post(f"/api/meetings/{meeting_d}/submit", {
    "response_type": "ACCEPT_PROPOSAL"
}, token=bob_token)
check("bob 接受，全员完成", data["data"]["all_submitted"] == True)
check("状态转为 ANALYZING", data["data"]["status"] == "ANALYZING")

# Agent 最终确认
code, data = post(f"/api/agent/meetings/{meeting_d}/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-26 11:00-11:30",
    "agent_reasoning": "双方接受妥协方案",
    "counter_proposals": []
})
check("最终 CONFIRMED", data["data"]["new_status"] == "CONFIRMED")


# ==========================================================
#  错误处理测试
# ==========================================================

section("错误处理测试")

# 对已 CONFIRMED 的会议提交
code, data = post(f"/api/meetings/{meeting_a}/submit", {
    "response_type": "INITIAL",
    "available_slots": ["2026-03-20 14:00-15:00"]
}, token=bob_token)
check("CONFIRMED 会议不允许提交 (400)", code == 400)
check("错误格式统一", data["code"] == 400)

# 对不存在的会议提交
code, data = post("/api/agent/meetings/mtg_nonexist/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-20 14:00-14:30",
    "agent_reasoning": "test",
    "counter_proposals": []
})
check("不存在的会议返回 404", code == 404)

# 对非 ANALYZING 的会议提交 Agent 结果
code, data = post(f"/api/agent/meetings/{meeting_a}/result", {
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-20 14:00-14:30",
    "agent_reasoning": "test",
    "counter_proposals": []
})
check("非 ANALYZING 状态拒绝 Agent 提交 (400)", code == 400)

# 无效的 response_type
code, data = post(f"/api/meetings/{meeting_a}/submit", {
    "response_type": "INVALID_TYPE",
    "available_slots": []
}, token=bob_token)
check("无效 response_type 返回 422", code == 422)


# ========== 最终报告 ==========

print(f"\n{'=' * 60}")
print(f"  测试完成: {passed} 通过 / {failed} 失败 / {passed + failed} 总计")
print(f"{'=' * 60}")

if failed > 0:
    sys.exit(1)
