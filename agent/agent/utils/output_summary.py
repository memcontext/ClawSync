#!/usr/bin/env python3
"""
summarize_meeting API
读取 meeting_score/{meeting_id}.json，由 LLM Agent 分析打分数据，
返回符合 API 8（POST /api/agent/meetings/{meeting_id}/result）的请求体格式。

时间槽 key 格式：YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM（日期感知）

公开接口：
    summarize_meeting(meeting_id, duration_minutes=30, initiator_id=None, ...) -> dict
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, field_validator

from .agent_input_format import DATA_DIR
from config import DOUBAO_API_KEY, DOUBAO_BASE_URL, DOUBAO_MODEL, LLM_TEMPERATURE

# ─── 配置 ────────────────────────────────────────────────────────────────────

SCORE_DIR = Path(__file__).resolve().parent.parent / "meeting_score"


def _score_file(meeting_id: str) -> Path:
    return SCORE_DIR / f"{meeting_id}.json"

# ─── Pydantic 模型 ────────────────────────────────────────────────────────────

class CoordinatorResult(BaseModel):
    """API 8 请求体格式。"""
    decision_status: Literal["CONFIRMED", "NEGOTIATING"]
    final_time: str | None = None
    agent_reasoning: str
    counter_proposals: list[dict] = []

    @field_validator("final_time")
    @classmethod
    def check_final_time(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # 格式：YYYY-MM-DD HH:MM-HH:MM（如 2026-03-21 15:00-16:00）
        pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}-\d{2}:\d{2}$"
        if not re.match(pattern, v):
            raise ValueError(f"final_time 格式错误：{v!r}，应为 YYYY-MM-DD HH:MM-HH:MM")
        return v

# ─── 时间块查找 ───────────────────────────────────────────────────────────────

def _get_initiator_slots(meeting_id: str, initiator_id: str) -> dict[str, bool]:
    """从 meeting_time_data 读取 initiator 的日期感知时间槽。"""
    path = DATA_DIR / f"{meeting_id}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data.get(initiator_id, {})
    return {
        k: v for k, v in entry.items()
        if "--" in str(k)  # 只取 slot key，排除 user_ID / meeting_ID
    }


def _are_consecutive(slot_a: str, slot_b: str) -> bool:
    """检查两个日期感知 slot key 是否时间连续。"""
    # slot_a: "2026-03-21 10:00--2026-03-21 10:30"
    # slot_b: "2026-03-21 10:30--2026-03-21 11:00"
    end_of_a = slot_a.split("--")[1]    # "2026-03-21 10:30"
    start_of_b = slot_b.split("--")[0]  # "2026-03-21 10:30"
    return end_of_a == start_of_b


def _find_candidate_blocks(
    score_data: dict,
    n_slots: int,
    initiator_slots: dict | None = None,
    total_participants: int = 1,
) -> list[dict]:
    """
    从日期感知的 score_data 中查找满足条件的连续时间块：
      1. 块内所有子槽时间连续
      2. 若提供 initiator_slots，块内所有子槽 initiator 必须为 True
      3. 块内所有子槽 score == total_participants（全员有空）

    Returns:
        [{"time": "2026-03-21 10:00-11:00", "min_score": 3, ...}, ...]
    """
    sorted_slots = sorted(score_data.keys())
    candidates = []

    for i in range(len(sorted_slots) - n_slots + 1):
        block = sorted_slots[i: i + n_slots]

        # ① 检查连续性
        consecutive = True
        for j in range(len(block) - 1):
            if not _are_consecutive(block[j], block[j + 1]):
                consecutive = False
                break
        if not consecutive:
            continue

        # ② initiator 必须在块内所有槽有空
        if initiator_slots is not None:
            if not all(initiator_slots.get(s) is True for s in block):
                continue

        # ③ 块内每个槽 score 必须等于参与者总人数
        scores = [score_data.get(s, {}).get("score", 0) for s in block]
        if min(scores) < total_participants:
            continue

        # 合并冲突
        conflicts_union: set[str] = set()
        for s in block:
            conflicts_union.update(score_data.get(s, {}).get("conflict", []))

        # 提取 final_time 格式：YYYY-MM-DD HH:MM-HH:MM
        block_start = block[0].split("--")[0]     # "2026-03-21 10:00"
        block_end_full = block[-1].split("--")[1]  # "2026-03-21 11:00"
        block_end_hm = block_end_full.split(" ")[1]  # "11:00"

        candidates.append({
            "time": f"{block_start}-{block_end_hm}",  # "2026-03-21 10:00-11:00"
            "min_score": min(scores),
            "total_score": sum(scores),
            "conflict_count": len(conflicts_union),
            "conflicts": sorted(conflicts_union),
        })

    candidates.sort(key=lambda x: (-x["min_score"], x["conflict_count"]))
    return candidates

# ─── 参与者摘要 ──────────────────────────────────────────────────────────────

def _build_participants_summary(
    participants_info: list[dict] | None,
    meeting_id: str,
    initiator_id: str | None,
) -> str:
    """构建参与者可用时间摘要文本，供 LLM 在 reasoning 中引用。"""
    if not participants_info:
        data_path = DATA_DIR / f"{meeting_id}.json"
        if not data_path.exists():
            return "（无参与者信息）"
        data = json.loads(data_path.read_text(encoding="utf-8"))
        lines = []
        for uid, entry in data.items():
            role = "发起人" if uid == initiator_id else "参与者"
            free = [k for k, v in entry.items() if "--" in str(k) and v is True]
            if free:
                lines.append(f"  [{role}] user_id={uid}: 可用时间 {', '.join(sorted(free))}")
            else:
                lines.append(f"  [{role}] user_id={uid}: 无明确可用时间")
        return "\n".join(lines)

    # 有 participants_info（来自 API 7），优先用 email
    lines = []
    sorted_info = sorted(
        participants_info,
        key=lambda p: 0 if p.get("role") == "initiator" else 1,
    )
    for p in sorted_info:
        role = "发起人" if p.get("role") == "initiator" else "参与者"
        email = p.get("email", f"user_id={p.get('user_id')}")
        slots = p.get("latest_slots") or []
        note = (p.get("preference_note") or "").strip()

        if slots:
            slot_strs = []
            for s in slots:
                if isinstance(s, dict) and "start" in s and "end" in s:
                    slot_strs.append(f"{s['start']}-{s['end']}")
                else:
                    slot_strs.append(f"(格式异常: {s})")
            lines.append(f"  [{role}] {email}: 可用时间 {', '.join(slot_strs)}")
        elif note:
            lines.append(f"  [{role}] {email}: 可用时间（自然语言）{note}")
        else:
            lines.append(f"  [{role}] {email}: 未提交时间信息")

    return "\n".join(lines)


# ─── LangChain Chain ──────────────────────────────────────────────────────────

def _build_confirmed_chain():
    llm = ChatOpenAI(
        model=DOUBAO_MODEL,
        api_key=DOUBAO_API_KEY,
        base_url=DOUBAO_BASE_URL,
        temperature=LLM_TEMPERATURE,
    )

    system_prompt = (
        "你是一个会议时间推荐助手，从候选时间块中选出最佳时间，以 json 格式输出。\n\n"
        "输入包含：\n"
        "1. 发起人信息（email + 可用时间段）\n"
        "2. 每位参与者信息（email + 可用时间段）\n"
        "3. 候选时间块列表（已确认全员有空的连续时间段）\n\n"
        "请直接输出一个 json 对象，包含以下字段：\n"
        "- decision_status : 固定字符串 CONFIRMED\n"
        "- final_time      : 选中的时间块（直接使用候选块的 time 字段值）\n"
        "- agent_reasoning : 选择该时间块的理由（中文，2-3句话）。"
        "必须说明：发起人 XXX 在 XX 时段有空，"
        "各参与者 XXX 在该时段也有空，全员无冲突。"
        "如果有多个候选，说明为何选这个而非其他。\n"
        "- counter_proposals : 固定为空数组\n\n"
        "规则：优先选 min_score 最高的，相同时选 conflict_count 最少的。"
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human",
         "会议 {meeting_id} 的参与者信息：\n\n{participants_summary}\n\n"
         "候选时间块：\n\n{candidates}"),
    ])

    return prompt | llm | JsonOutputParser()


def _build_negotiating_chain():
    llm = ChatOpenAI(
        model=DOUBAO_MODEL,
        api_key=DOUBAO_API_KEY,
        base_url=DOUBAO_BASE_URL,
        temperature=LLM_TEMPERATURE,
    )

    system_prompt = (
        "你是一个会议协调助手，分析为何找不到合适的会议时间，以 json 格式输出。\n\n"
        "冲突分析以发起人（initiator）为基准：\n"
        "- 先列出发起人的可用时间段\n"
        "- 逐个对比每位参与者，找出谁与发起人的时间不重叠\n\n"
        "请直接输出一个 json 对象，包含以下字段：\n"
        "- decision_status   : 固定字符串 NEGOTIATING\n"
        "- final_time        : 固定为 null\n"
        "- agent_reasoning   : 协商失败原因（中文，2-3句话）。"
        "必须说明：发起人 XXX 的可用时间为 XX-XX，"
        "XXX（具体email）与发起人时间冲突（说明其可用时间），"
        "因此无法找到全员有空的时间段。\n"
        "- counter_proposals : 固定为空数组"
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", (
            "会议 {meeting_id} 无法找到满足条件的时间块。\n"
            "会议时长要求：{duration_minutes} 分钟\n\n"
            "参与者信息：\n{participants_summary}\n\n"
            "发起人可用时间槽：\n{initiator_slots}\n\n"
            "各时间段打分数据（部分）：\n{score_sample}"
        )),
    ])

    return prompt | llm | JsonOutputParser()

# ─── 公开 API ─────────────────────────────────────────────────────────────────

def summarize_meeting(
    meeting_id: str,
    duration_minutes: int = 30,
    initiator_id: str | None = None,
    total_participants: int | None = None,
    participants_info: list[dict] | None = None,
    meeting_date: str | None = None,
) -> dict:
    """
    分析会议打分数据，返回 API 8 格式的请求体。

    Args:
        meeting_id        : 会议唯一编号
        duration_minutes  : 会议时长（分钟）
        initiator_id      : 发起人 user_id
        total_participants: 参与者总人数；None 则自动推断
        participants_info : API 7 的 participants_data 列表
        meeting_date      : 会议日期（不再用于 slot key，兼容保留）
    """
    path = _score_file(meeting_id)
    if not path.exists():
        raise FileNotFoundError(
            f"找不到打分文件：{path}，请先调用 score_meeting('{meeting_id}')"
        )

    score_data: dict = json.loads(path.read_text(encoding="utf-8"))

    n_slots = max(1, (duration_minutes + 29) // 30)

    # 读取 initiator 的时间槽
    initiator_slots: dict | None = None
    if initiator_id is not None:
        initiator_slots = _get_initiator_slots(meeting_id, initiator_id)

    # 自动推断参与者总人数
    if total_participants is None:
        data_path = DATA_DIR / f"{meeting_id}.json"
        if data_path.exists():
            data = json.loads(data_path.read_text(encoding="utf-8"))
            total_participants = len(data)
        else:
            total_participants = 1

    # 查找候选时间块
    candidates = _find_candidate_blocks(
        score_data, n_slots, initiator_slots, total_participants
    )

    # 构建参与者摘要
    participants_summary = _build_participants_summary(
        participants_info, meeting_id, initiator_id
    )

    if candidates:
        # ── CONFIRMED ────────────────────────────────────────────────────────
        raw: dict = _build_confirmed_chain().invoke({
            "meeting_id": meeting_id,
            "participants_summary": participants_summary,
            "candidates": json.dumps(candidates[:5], ensure_ascii=False, indent=2),
        })
        raw.setdefault("decision_status", "CONFIRMED")
        raw.setdefault("counter_proposals", [])

    else:
        # ── NEGOTIATING ──────────────────────────────────────────────────────
        # initiator 有空的时间槽（完整日期格式）
        initiator_free = (
            [k for k, v in initiator_slots.items() if v is True]
            if initiator_slots else []
        )
        # 取前10个有冲突的时间段
        conflict_sample = {
            slot: info
            for slot, info in score_data.items()
            if info.get("conflict")
        }
        sample = dict(list(conflict_sample.items())[:10])

        raw: dict = _build_negotiating_chain().invoke({
            "meeting_id": meeting_id,
            "duration_minutes": duration_minutes,
            "participants_summary": participants_summary,
            "initiator_slots": json.dumps(sorted(initiator_free), ensure_ascii=False),
            "score_sample": json.dumps(sample, ensure_ascii=False, indent=2),
        })
        raw.setdefault("decision_status", "NEGOTIATING")
        raw.setdefault("final_time", None)
        raw.setdefault("counter_proposals", [])

    result = CoordinatorResult.model_validate(raw)
    return result.model_dump()
