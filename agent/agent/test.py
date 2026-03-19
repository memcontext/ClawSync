#!/usr/bin/env python3
"""
测试 coordinate_from_task：模拟 API 7 输入，验证 API 8 输出。
包含 4 个场景：2 个无冲突（CONFIRMED）+ 2 个有冲突（NEGOTIATING）。
"""
import json
from utils import coordinate_from_task

# ═══════════════════════════════════════════════════════════════════════════════
# 场景 1：无冲突 / 30 分钟会议
#   Alice (initiator)  : 18:00-20:00
#   Bob  (participant)  : 18:00-19:00
#   Carol (participant) : 自然语言 "今晚全程有空"
#   → 三人在 18:00-19:00 均有空，30 分钟即可，预期 CONFIRMED
# ═══════════════════════════════════════════════════════════════════════════════
task_1 = {
    "meeting_id": "test_no_conflict_30min",
    "title": "场景1：无冲突 30 分钟",
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
            "preference_note": "今晚全程有空",
        },
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
# 场景 2：无冲突 / 60 分钟会议（需要连续 2 个槽）
#   Alice (initiator)  : 14:00-17:00
#   Bob  (participant)  : 15:00-17:00
#   Carol (participant) : 14:30-18:00
#   → 三人在 15:00-17:00 均有空，60 分钟连续块存在，预期 CONFIRMED
# ═══════════════════════════════════════════════════════════════════════════════
task_2 = {
    "meeting_id": "test_no_conflict_60min",
    "title": "场景2：无冲突 60 分钟",
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
# 场景 3：有冲突 / 时间完全不重叠
#   Alice (initiator)  : 09:00-11:00（上午）
#   Bob  (participant)  : 18:00-20:00（晚上）
#   Carol (participant) : 14:00-16:00（下午）
#   → 三人时间段毫无交集，预期 NEGOTIATING
# ═══════════════════════════════════════════════════════════════════════════════
task_3 = {
    "meeting_id": "test_conflict_no_overlap",
    "title": "场景3：完全无交集",
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
# 场景 4：有冲突 / 交集时间不够长（duration 太大）
#   Alice (initiator)  : 18:00-19:00
#   Bob  (participant)  : 18:00-19:00
#   Carol (participant) : 18:00-19:00
#   → 三人都在 18:00-19:00 有空（60 分钟），但会议需要 90 分钟
#   → 只有 2 个连续槽（60 分钟），不满足 3 个槽（90 分钟），预期 NEGOTIATING
# ═══════════════════════════════════════════════════════════════════════════════
task_4 = {
    "meeting_id": "test_conflict_duration_too_long",
    "title": "场景4：交集不够长",
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
# 运行所有场景
# ═══════════════════════════════════════════════════════════════════════════════

tasks = [task_1, task_2, task_3, task_4]

for i, task in enumerate(tasks, 1):
    print(f"\n{'='*60}")
    print(f"  场景 {i}：{task['title']}")
    print(f"  meeting_id: {task['meeting_id']}")
    print(f"  duration_minutes: {task['duration_minutes']}")
    print(f"{'='*60}\n")

    result = coordinate_from_task(task)

    print(f"\n  结果：")
    print(json.dumps(result, ensure_ascii=False, indent=4))

    # 简单断言
    expected = "CONFIRMED" if i <= 2 else "NEGOTIATING"
    status = result.get("decision_status", "UNKNOWN")
    ok = "PASS" if status == expected else "FAIL"
    print(f"\n  [{ok}] 预期 {expected}，实际 {status}")
    print()
