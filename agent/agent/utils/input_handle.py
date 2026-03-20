#!/usr/bin/env python3
"""
handle_meeting / coordinate_meeting / coordinate_from_task API

公开接口：
    handle_meeting(role_inputs, meeting_id) -> dict          # 收集+打分
    coordinate_meeting(role_inputs, meeting_id) -> dict      # 收集+打分+LLM推荐
    coordinate_from_task(task) -> dict                       # 直接接收 API 7 格式
"""

import json

from .agent_input_format import submit_user_time
from .output_summary import summarize_meeting
from .scoring import score_meeting
from .logger import get_logger

logger = get_logger("input_handle")

# ─── 公开 API ─────────────────────────────────────────────────────────────────

def handle_meeting(
    role_inputs: list[tuple[str, "str | list[dict]"]],
    meeting_id: str,
    reference_date: str | None = None,
) -> dict:
    """
    收集会议所有参与者的时间描述，格式化存储后进行打分。

    Args:
        role_inputs : 参与者列表，每项为 (user_id, user_input) 元组。
                      user_id    —— 用户唯一标识
                      user_input —— 时间描述，两种格式均可：
                          · 自然语言字符串：如 "我今晚 6 点到 7 点半有空"
                          · 标准 API 格式：如 [{"start": "2026-03-18 14:00",
                                                "end":   "2026-03-18 16:00"}, ...]
        meeting_id  : 会议唯一编号

    Returns:
        会议打分结果 dict，格式：
        {
            "18:00-18:30": {"score": 2, "conflict": ["user_003"]},
            "18:30-19:00": {"score": 3, "conflict": []},
            ...
        }

    Example:
        result = handle_meeting(
            role_inputs=[
                ("user_001", "我今晚 6 点到 7 点半有空"),
                ("user_002", [{"start": "2026-03-18 18:30", "end": "2026-03-18 19:00"}]),
                ("user_003", "今晚全程有空"),
            ],
            meeting_id="meeting_12345",
        )
    """
    # ── 第一阶段：逐用户格式化时间描述 ──────────────────────────────────────
    logger.info("handle_meeting 开始: meeting=%s, 参与者=%d", meeting_id, len(role_inputs))
    for user_id, user_input in role_inputs:
        if isinstance(user_input, list):
            logger.info("[%s] 标准格式，直接解析（%d 个时间段）", user_id, len(user_input))
        else:
            logger.info("[%s] 自然语言，调用 Agent 解析：「%s」", user_id, user_input)
        submit_user_time(
            user_input=user_input,
            user_id=user_id,
            meeting_id=meeting_id,
            reference_date=reference_date,
        )

    # ── 第二阶段：汇总打分 ────────────────────────────────────────────────────
    logger.info("正在对 %s 打分...", meeting_id)
    score = score_meeting(meeting_id)

    return score


def coordinate_meeting(
    role_inputs: list[tuple[str, "str | list[dict]"]],
    meeting_id: str,
    reference_date: str | None = None,
) -> dict:
    """
    一站式会议时间协调：收集用户时间 → 打分 → LLM 推荐。

    Args:
        role_inputs : 参与者列表，每项为 (user_id, user_input) 元组。
                      user_input 支持自然语言字符串或标准 API 格式 list[dict]。
        meeting_id  : 会议唯一编号

    Returns:
        coordinator_result dict，两种格式之一：

        找到共同空闲时间（CONFIRMED）：
        {
            "status": "CONFIRMED",
            "final_time": "2026-01-01 18:00-2026-01-01 18:30",
            "reasoning": "该时间段有 2 人有空且无冲突",
            "alternative_slots": ["2026-01-01 18:30-2026-01-01 19:00"]
        }

        无共同空闲时间（NEGOTIATING）：
        {
            "status": "NEGOTIATING",
            "reasoning": "所有时间段均存在冲突",
            "suggestions": ["建议扩大可用时间范围"]
        }
    """
    handle_meeting(role_inputs=role_inputs, meeting_id=meeting_id, reference_date=reference_date)
    logger.info("正在分析 %s 推荐时间...", meeting_id)
    return summarize_meeting(meeting_id)


def coordinate_from_task(task: dict) -> dict:
    """
    直接接收 API 7（GET /api/agent/tasks/pending）返回的单个 task 对象，
    执行完整协调流程，返回 API 8 格式的请求体。

    Args:
        task: API 7 pending_tasks 中的单个任务，结构示例：
            {
                "meeting_id": "mtg_xxx",
                "title": "项目讨论会",
                "participants_data": [
                    {
                        "user_id": 1,
                        "email": "alice@example.com",
                        "role": "initiator",
                        "latest_slots": [
                            {"start": "2026-03-18 14:00", "end": "2026-03-18 16:00"}
                        ],
                        "preference_note": "尽量安排在上午"
                    },
                    ...
                ]
            }

    Returns:
        API 8 格式 dict：
        {
            "decision_status": "CONFIRMED",
            "final_time": "2026-01-01 15:00-15:30",
            "agent_reasoning": "...",
            "counter_proposals": []
        }
    """
    meeting_id: str = task["meeting_id"]
    duration_minutes: int = task.get("duration_minutes", 30)
    round_count: int = task.get("round_count", 0)
    max_rounds: int = task.get("max_rounds", 3)
    previous_reasoning: str | None = task.get("previous_reasoning")
    participants_data: list[dict] = task["participants_data"]

    logger.info("=" * 60)
    logger.info("coordinate_from_task 开始")
    logger.info("  meeting_id      : %s", meeting_id)
    logger.info("  title           : %s", task.get("title", "N/A"))
    logger.info("  duration_minutes: %d", duration_minutes)
    logger.info("  round_count     : %d / max_rounds: %d", round_count, max_rounds)
    logger.info("  participants    : %d 人", len(participants_data))
    for p in participants_data:
        logger.info("    [%s] %s (user_id=%s), slots=%s, note=%r",
                     p.get("role", "?"), p.get("email", "?"), p.get("user_id", "?"),
                     json.dumps(p.get("latest_slots") or [], ensure_ascii=False),
                     p.get("preference_note"))
    if previous_reasoning:
        logger.info("  previous_reasoning: %s", previous_reasoning)
    logger.info("=" * 60)

    # ── FAILED：超过最大协商轮次 ────────────────────────────────────────────
    if round_count >= max_rounds:
        logger.warning("%s 已达最大协商轮次 (%d/%d)，返回 FAILED", meeting_id, round_count, max_rounds)
        return {
            "decision_status": "FAILED",
            "final_time": None,
            "agent_reasoning": f"经过 {max_rounds} 轮协商，参与者依然无法达成一致的会议时间。",
            "counter_proposals": [],
        }

    # ── 校验：有且仅有一个 initiator ─────────────────────────────────────────
    initiators = [p for p in participants_data if p.get("role") == "initiator"]
    if len(initiators) == 0:
        return {
            "decision_status": "NEGOTIATING",
            "final_time": None,
            "agent_reasoning": "错误：会议中未找到发起人（initiator），无法进行时间协调",
            "counter_proposals": [],
        }
    if len(initiators) > 1:
        emails = [p.get("email", str(p["user_id"])) for p in initiators]
        return {
            "decision_status": "NEGOTIATING",
            "final_time": None,
            "agent_reasoning": f"错误：会议存在多个发起人（{emails}），每次会议只允许一个发起人",
            "counter_proposals": [],
        }

    initiator_id = str(initiators[0]["user_id"])

    # ── 从 latest_slots 中提取真实日期（取第一个有效的日期）────────────────────
    meeting_date: str | None = None
    for p in participants_data:
        for slot in (p.get("latest_slots") or []):
            start_str = str(slot.get("start", ""))
            if " " in start_str:
                meeting_date = start_str.split(" ")[0]  # "2026-03-21"
                break
        if meeting_date:
            break

    # ── 收集每位参与者的时间输入 ──────────────────────────────────────────────
    import re
    _SLOT_FMT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")  # YYYY-MM-DD HH:MM

    role_inputs: list[tuple[str, "str | list[dict]"]] = []
    for p in participants_data:
        user_id = str(p["user_id"])
        email = p.get("email", user_id)
        slots: list[dict] = p.get("latest_slots") or []
        note: str = (p.get("preference_note") or "").strip()

        if slots:
            # 校验 latest_slots 格式
            valid = True
            for i, slot in enumerate(slots):
                if not isinstance(slot, dict):
                    logger.error("[%s] latest_slots[%d] 不是 dict：%s", email, i, slot)
                    valid = False
                    continue
                start_val = slot.get("start")
                end_val = slot.get("end")
                if start_val is None or end_val is None:
                    logger.error("[%s] latest_slots[%d] 缺少 start/end 字段：%s", email, i, slot)
                    valid = False
                elif not _SLOT_FMT.match(str(start_val)) or not _SLOT_FMT.match(str(end_val)):
                    logger.error("[%s] latest_slots[%d] 格式错误，应为 'YYYY-MM-DD HH:MM'，实际 start=%r, end=%r",
                                email, i, start_val, end_val)
                    valid = False

            if valid:
                role_inputs.append((user_id, slots))
            else:
                logger.warning("[%s] latest_slots 校验失败，跳过该参与者", email)
        elif note:
            role_inputs.append((user_id, note))
        else:
            logger.warning("[%s] 无时间数据（latest_slots 和 preference_note 均为空），跳过", email)

    handle_meeting(role_inputs=role_inputs, meeting_id=meeting_id, reference_date=meeting_date)
    logger.info("正在分析 %s 推荐时间（时长 %d 分钟，轮次 %d/%d，优先对齐发起人）...",
                meeting_id, duration_minutes, round_count, max_rounds)
    result = summarize_meeting(
        meeting_id=meeting_id,
        duration_minutes=duration_minutes,
        initiator_id=initiator_id,
        participants_info=participants_data,
        meeting_date=meeting_date,
        round_count=round_count,
        max_rounds=max_rounds,
        previous_reasoning=previous_reasoning,
    )
    logger.info("coordinate_from_task 完成: meeting=%s, decision=%s", meeting_id, result.get("decision_status"))
    logger.info("输出结果: %s", json.dumps(result, ensure_ascii=False, indent=2))
    return result
