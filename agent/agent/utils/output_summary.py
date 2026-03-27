#!/usr/bin/env python3
"""
summarize_meeting API
读取 meeting_score/{meeting_id}.json，由 LLM Agent 分析打分数据，
返回符合 API 8（POST /api/agent/meetings/{meeting_id}/result）的请求体格式。

支持三种决策状态：
  - CONFIRMED   : 找到全员有空的时间块
  - NEGOTIATING : 无全员有空时间块，生成 counter_proposals 给冲突用户
  - FAILED      : 超过最大协商轮次

公开接口：
    summarize_meeting(meeting_id, duration_minutes=30, initiator_id=None, ...) -> dict
"""

import json
import re
from pathlib import Path
from typing import Literal

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, field_validator

from .agent_input_format import DATA_DIR
from .logger import get_logger
from config import (
    DOUBAO_API_KEY, DOUBAO_BASE_URL, DOUBAO_MODEL, LLM_TEMPERATURE,
    NEGOTIATION_TOP_K,
)

logger = get_logger("output_summary")

# ─── 配置 ────────────────────────────────────────────────────────────────────

SCORE_DIR = Path(__file__).resolve().parent.parent / "meeting_score"


def _score_file(meeting_id: str) -> Path:
    return SCORE_DIR / f"{meeting_id}.json"

# ─── Pydantic 模型 ────────────────────────────────────────────────────────────

class CoordinatorResult(BaseModel):
    """API 8 请求体格式。"""
    decision_status: Literal["CONFIRMED", "NEGOTIATING", "FAILED"]
    final_time: str | None = None
    agent_reasoning: str
    counter_proposals: list[dict] = []

    @field_validator("final_time")
    @classmethod
    def check_final_time(cls, v: str | None) -> str | None:
        if v is None:
            return v
        pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}-\d{2}:\d{2}$"
        if not re.match(pattern, v):
            raise ValueError(f"final_time 格式错误：{v!r}，应为 YYYY-MM-DD HH:MM-HH:MM")
        return v

# ─── 时间计算辅助 ─────────────────────────────────────────────────────────────


def _slot_duration_minutes(start_str: str, end_str: str) -> int:
    """计算两个时间戳之间的分钟数"""
    from datetime import datetime
    try:
        start = datetime.fromisoformat(start_str)
        end = datetime.fromisoformat(end_str)
        return max(0, int((end - start).total_seconds() / 60))
    except (ValueError, TypeError):
        return 0


def _check_duration_capacity(participants_info: list, duration_minutes: int) -> list:
    """规则兜底：检查参会人可用时间是否满足会议时长"""
    insufficient = []
    for p in participants_info:
        slots = p.get("latest_slots") or []
        if not slots:
            continue
        total_minutes = sum(
            _slot_duration_minutes(s.get("start", ""), s.get("end", ""))
            for s in slots if isinstance(s, dict)
        )
        if 0 < total_minutes < duration_minutes:
            insufficient.append({
                "email": p.get("email", f"user_id={p.get('user_id')}"),
                "available_minutes": total_minutes,
            })
    return insufficient


# ─── 会议结构变更检测 ─────────────────────────────────────────────────────────

def _detect_preference_issues(participants_info: list[dict], duration_minutes: int = 0) -> dict | None:
    """
    用 LLM 统一检测参与者的问题，包括：
      1. 拒绝参会（明确表示不参加、拒绝）
      2. 会议结构变更（修改时长、拆分会议、增减人员、改变形式等）
      3. 可用时间不足（参会人的可用时间总长 < 会议所需时长）

    返回 {"type": "rejected"|"structural_change"|"capacity_mismatch", "detail": "..."} 或 None。
    """
    notes = []
    for p in participants_info:
        note = (p.get("preference_note") or "").strip()
        email = p.get("email", "unknown")
        slots = p.get("latest_slots") or []

        # 计算参会人可用时间总长
        total_minutes = sum(
            _slot_duration_minutes(s.get("start", ""), s.get("end", ""))
            for s in slots if isinstance(s, dict)
        )
        capacity_info = f"（可用时间: {total_minutes}分钟）" if total_minutes > 0 else "（未提交时间）"

        if note:
            notes.append(f"{email}{capacity_info}: {note}")
        elif duration_minutes > 0 and 0 < total_minutes < duration_minutes:
            notes.append(f"{email}{capacity_info}: [用户未备注，但提交的可用时间不足会议所需的{duration_minutes}分钟]")

    if not notes:
        return None

    llm = ChatOpenAI(
        model=DOUBAO_MODEL,
        api_key=DOUBAO_API_KEY,
        base_url=DOUBAO_BASE_URL,
        temperature=0,
    )

    duration_context = f"会议所需时长为 {duration_minutes} 分钟。" if duration_minutes > 0 else ""

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            f"你是一个会议协调助手。{duration_context}\n"
            "分析以下参与者备注和可用时间，判断是否存在以下三类问题：\n\n"
            "1. **拒绝参会**：参与者明确表示不参加、拒绝会议、无法出席。\n"
            "   标记为 [已拒绝] 的备注一定是拒绝。\n"
            "   例如：「我不参加了」「这个会我就不去了」「[已拒绝] 时间不合适」\n\n"
            "2. **会议结构变更**：参与者建议修改会议本身的设置，而非单纯的时间偏好。\n"
            "   包括但不限于：修改时长、拆分会议、增减人员、改变形式（线上/线下/邮件代替）。\n"
            "   例如：「建议改成30分钟」「建议分两次开」「建议也叫上某某」「改成邮件沟通吧」\n\n"
            "3. **可用时间不足**：参与者提交的可用时间总长小于会议所需时长。\n"
            "   这意味着即使协商也无法满足会议需求，属于隐式的结构性问题。\n"
            "   例如：会议需要120分钟，但参与者只有30分钟可用\n"
            "   注意：「我只能参加30分钟」「我只有1小时」这类表述也属于此类\n\n"
            "纯时间偏好（如「我更喜欢下午」「周五不方便」「尽量安排在上午」）不属于以上任何一类。\n\n"
            "请输出一行：\n"
            "- 如果检测到拒绝：rejected: 简短描述（如 bob@x.com 拒绝参会，原因：时间不合适）\n"
            "- 如果检测到结构变更：structural_change: 简短描述（如 bob@x.com 建议将时长改为30分钟）\n"
            "- 如果检测到可用时间不足：capacity_mismatch: 简短描述（如 bob@x.com 仅有30分钟可用，不足会议所需120分钟）\n"
            "- 优先级：rejected > structural_change > capacity_mismatch\n"
            "- 如果都没有：无"
        )),
        ("human", "参与者备注：\n{notes}"),
    ])

    try:
        result = (prompt | llm).invoke({"notes": "\n".join(notes)})
        answer = result.content.strip()
        if answer == "无" or not answer:
            return None
        if answer.startswith("rejected:"):
            return {"type": "rejected", "detail": answer[len("rejected:"):].strip()}
        if answer.startswith("structural_change:"):
            return {"type": "structural_change", "detail": answer[len("structural_change:"):].strip()}
        if answer.startswith("capacity_mismatch:"):
            return {"type": "capacity_mismatch", "detail": answer[len("capacity_mismatch:"):].strip()}
        # 兜底：无法解析的格式视为无问题
        logger.warning("preference_note 检测返回未知格式: %s", answer)
        return None
    except Exception as e:
        logger.warning("preference_note LLM 检测失败，跳过: %s", e)
        return None


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
        if "--" in str(k)
    }


def _are_consecutive(slot_a: str, slot_b: str) -> bool:
    """检查两个日期感知 slot key 是否时间连续。"""
    end_of_a = slot_a.split("--")[1]
    start_of_b = slot_b.split("--")[0]
    return end_of_a == start_of_b


def _slot_to_time_str(block: list[str]) -> str:
    """将连续 slot key 列表转为 'YYYY-MM-DD HH:MM-HH:MM' 格式。"""
    block_start = block[0].split("--")[0]       # "2026-03-21 10:00"
    block_end_full = block[-1].split("--")[1]   # "2026-03-21 11:00"
    block_end_hm = block_end_full.split(" ")[1] # "11:00"
    return f"{block_start}-{block_end_hm}"


def _find_candidate_blocks(
    score_data: dict,
    n_slots: int,
    initiator_slots: dict | None = None,
    total_participants: int = 1,
) -> list[dict]:
    """
    查找全员有空的连续时间块（CONFIRMED 候选）。
    """
    sorted_slots = sorted(score_data.keys())
    candidates = []

    for i in range(len(sorted_slots) - n_slots + 1):
        block = sorted_slots[i: i + n_slots]

        consecutive = True
        for j in range(len(block) - 1):
            if not _are_consecutive(block[j], block[j + 1]):
                consecutive = False
                break
        if not consecutive:
            continue

        if initiator_slots is not None:
            if not all(initiator_slots.get(s) is True for s in block):
                continue

        scores = [score_data.get(s, {}).get("score", 0) for s in block]
        if min(scores) < total_participants:
            continue

        conflicts_union: set[str] = set()
        for s in block:
            conflicts_union.update(score_data.get(s, {}).get("conflict", []))

        candidates.append({
            "time": _slot_to_time_str(block),
            "min_score": min(scores),
            "total_score": sum(scores),
            "conflict_count": len(conflicts_union),
            "conflicts": sorted(conflicts_union),
        })

    candidates.sort(key=lambda x: (-x["min_score"], x["conflict_count"]))
    return candidates


def _find_negotiation_blocks(
    score_data: dict,
    n_slots: int,
    initiator_slots: dict,
    initiator_id: str,
) -> list[dict]:
    """
    查找 initiator 有空但存在冲突的连续时间块（NEGOTIATING 候选）。
    按冲突人数升序排列，相同冲突数随机打乱，只返回 top-1。

    Returns:
        [{"time": "2026-03-21 07:00-08:00", "conflict_count": 1, "conflicts": ["4"]}]
    """
    sorted_slots = sorted(score_data.keys())
    candidates = []

    for i in range(len(sorted_slots) - n_slots + 1):
        block = sorted_slots[i: i + n_slots]

        # 检查连续性
        consecutive = True
        for j in range(len(block) - 1):
            if not _are_consecutive(block[j], block[j + 1]):
                consecutive = False
                break
        if not consecutive:
            continue

        # initiator 必须在块内所有槽有空
        if not all(initiator_slots.get(s) is True for s in block):
            continue

        # 收集冲突用户（排除 initiator）
        conflicts_union: set[str] = set()
        for s in block:
            conflicts_union.update(score_data.get(s, {}).get("conflict", []))
        conflicts_union.discard(initiator_id)

        # 必须有冲突（否则是 CONFIRMED）
        if not conflicts_union:
            continue

        candidates.append({
            "time": _slot_to_time_str(block),
            "conflict_count": len(conflicts_union),
            "conflicts": sorted(conflicts_union),
        })

    # 冲突人数少的排前面，相同冲突数的随机打乱
    import random
    random.shuffle(candidates)  # 先随机打乱，保证相同 conflict_count 时随机选取
    candidates.sort(key=lambda x: x["conflict_count"])
    return candidates[:1]  # 只返回 top-1


def _build_counter_proposals(
    negotiation_blocks: list[dict],
    participants_info: list[dict],
    initiator_id: str,
) -> list[dict]:
    """
    根据协商候选块构建 counter_proposals。

    每个冲突用户收到一条建议，包含冲突最少的 top-K 时间段。
    发起人不会出现在 counter_proposals 中。
    """
    if not negotiation_blocks:
        return []

    # 收集所有冲突用户 ID
    conflict_user_ids: set[str] = set()
    for block in negotiation_blocks:
        conflict_user_ids.update(block["conflicts"])
    conflict_user_ids.discard(initiator_id)

    # 建议时间段列表
    suggested_slots = [block["time"] for block in negotiation_blocks]

    # user_id → email 映射
    id_to_email = {
        str(p["user_id"]): p.get("email", f"user_{p['user_id']}")
        for p in participants_info
    }

    proposals = []
    for uid in sorted(conflict_user_ids):
        email = id_to_email.get(uid, uid)
        proposals.append({
            "target_email": email,
            "message": "以下是经过评估得到的需要你进行协调的时间：",
            "suggested_slots": suggested_slots,
        })

    return proposals


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
        "- 逐个对比每位参与者，找出谁与发起人的时间不重叠\n"
        "- 如果有上一轮的分析结果（previous_reasoning），避免重复相同建议\n\n"
        "请直接输出一个 json 对象，只包含以下两个字段：\n"
        "- decision_status   : 固定字符串 NEGOTIATING\n"
        "- agent_reasoning   : 协商失败原因（中文，2-3句话）。"
        "必须说明：发起人 XXX 的可用时间为 XX-XX，"
        "XXX（具体email）与发起人时间冲突（说明其可用时间），"
        "因此无法找到全员有空的时间段。\n\n"
        "注意：不要输出 final_time 和 counter_proposals 字段，这些由系统自动填充。"
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", (
            "会议 {meeting_id} 无法找到满足条件的时间块。\n"
            "会议时长要求：{duration_minutes} 分钟\n"
            "当前协商轮次：{round_count}\n\n"
            "参与者信息：\n{participants_summary}\n\n"
            "发起人可用时间槽：\n{initiator_slots}\n\n"
            "冲突最少的候选时间块（发起人有空但部分参与者冲突）：\n{negotiation_blocks}\n\n"
            "上一轮分析结果：\n{previous_reasoning}"
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
    round_count: int = 0,
    max_rounds: int = 3,
    previous_reasoning: str | None = None,
) -> dict:
    """
    分析会议打分数据，返回 API 8 格式的请求体。

    决策逻辑：
      1. round_count >= max_rounds → FAILED
      2. 找到全员有空连续块   → CONFIRMED
      3. 否则                 → NEGOTIATING（含 counter_proposals）
    """
    # ── FAILED：超过最大协商轮次 ───────────────────────────────────────────────
    if round_count >= max_rounds:
        return {
            "decision_status": "FAILED",
            "final_time": None,
            "agent_reasoning": f"经过 {max_rounds} 轮协商，参与者依然无法达成一致的会议时间。",
            "counter_proposals": [],
        }

    # ── FAILED：LLM 统一检测（拒绝 / 结构变更 / 可用时间不足） ─────────────
    if participants_info:
        issue = _detect_preference_issues(participants_info, duration_minutes)
        if issue:
            if issue["type"] == "rejected":
                logger.info("LLM 检测到参与者拒绝，直接 FAILED: %s", issue["detail"])
                return {
                    "decision_status": "FAILED",
                    "final_time": None,
                    "agent_reasoning": f"参与者拒绝了会议邀请：{issue['detail']}",
                    "counter_proposals": [],
                }
            elif issue["type"] == "structural_change":
                logger.info("LLM 检测到会议结构变更请求，直接 FAILED: %s", issue["detail"])
                return {
                    "decision_status": "FAILED",
                    "final_time": None,
                    "agent_reasoning": f"有参与者提出了会议结构调整建议，当前会议设置需要发起人重新确认：{issue['detail']}",
                    "counter_proposals": [],
                }
            elif issue["type"] == "capacity_mismatch":
                logger.info("LLM 检测到参会人可用时间不足: %s", issue["detail"])
                return {
                    "decision_status": "FAILED",
                    "final_time": None,
                    "agent_reasoning": f"参会人可用时间不足以满足会议需求：{issue['detail']}。建议发起人调整会议时长或与参会人协调。",
                    "counter_proposals": [],
                }

    # ── 规则兜底：参会人可用时间不足 ──────────────────────────────────────
    if participants_info:
        insufficient = _check_duration_capacity(participants_info, duration_minutes)
        if insufficient:
            detail = "；".join(f"{p['email']} 仅有 {p['available_minutes']} 分钟" for p in insufficient)
            logger.info("[%s] 规则兜底-参会人可用时间不足: %s", meeting_id, detail)
            return {
                "decision_status": "FAILED",
                "final_time": None,
                "agent_reasoning": f"会议需要 {duration_minutes} 分钟，但以下参会人的可用时间不足：{detail}。建议发起人调整会议时长或与参会人协调。",
                "counter_proposals": [],
            }

    path = _score_file(meeting_id)
    if not path.exists():
        raise FileNotFoundError(
            f"找不到打分文件：{path}，请先调用 score_meeting('{meeting_id}')"
        )

    score_data: dict = json.loads(path.read_text(encoding="utf-8"))
    n_slots = max(1, (duration_minutes + 29) // 30)
    logger.info("summarize_meeting: meeting=%s, duration=%dmin, n_slots=%d, initiator=%s",
                meeting_id, duration_minutes, n_slots, initiator_id)

    # 读取 initiator 的时间槽
    initiator_slots: dict | None = None
    if initiator_id is not None:
        initiator_slots = _get_initiator_slots(meeting_id, initiator_id)
        initiator_true = sum(1 for v in initiator_slots.values() if v is True)
        logger.debug("initiator 可用槽数: %d", initiator_true)

    # 自动推断参与者总人数
    if total_participants is None:
        data_path = DATA_DIR / f"{meeting_id}.json"
        if data_path.exists():
            data = json.loads(data_path.read_text(encoding="utf-8"))
            total_participants = len(data)
        else:
            total_participants = 1
    logger.info("参与者总人数: %d", total_participants)

    # 查找 CONFIRMED 候选块
    candidates = _find_candidate_blocks(
        score_data, n_slots, initiator_slots, total_participants
    )
    logger.info("CONFIRMED 候选块数量: %d", len(candidates))
    for c in candidates[:5]:
        logger.debug("  候选: %s (min_score=%d, conflict=%d)", c["time"], c["min_score"], c["conflict_count"])

    # 构建参与者摘要
    participants_summary = _build_participants_summary(
        participants_info, meeting_id, initiator_id
    )

    if candidates:
        # ── CONFIRMED ────────────────────────────────────────────────────────
        logger.info("[LLM Agent] 调用 CONFIRMED 链...")
        raw: dict = _build_confirmed_chain().invoke({
            "meeting_id": meeting_id,
            "participants_summary": participants_summary,
            "candidates": json.dumps(candidates[:5], ensure_ascii=False, indent=2),
        })
        raw.setdefault("decision_status", "CONFIRMED")
        raw.setdefault("counter_proposals", [])
        logger.info("[LLM Agent] CONFIRMED 链返回: %s", json.dumps(raw, ensure_ascii=False))
        result = CoordinatorResult.model_validate(raw)
        return result.model_dump()

    # ── NEGOTIATING ──────────────────────────────────────────────────────────
    logger.info("无 CONFIRMED 候选块，进入 NEGOTIATING 流程")

    # 查找冲突最少的候选块（initiator 有空但部分参与者冲突）
    negotiation_blocks = _find_negotiation_blocks(
        score_data, n_slots, initiator_slots or {},
        initiator_id or "",
    )

    logger.info("协商候选块 (top-1): %s", json.dumps(negotiation_blocks, ensure_ascii=False))

    # initiator 可用时间
    initiator_free = (
        sorted([k for k, v in initiator_slots.items() if v is True])
        if initiator_slots else []
    )

    # LLM 生成 reasoning
    logger.info("[LLM Agent] 调用 NEGOTIATING 链...")
    raw: dict = _build_negotiating_chain().invoke({
        "meeting_id": meeting_id,
        "duration_minutes": duration_minutes,
        "round_count": round_count,
        "participants_summary": participants_summary,
        "initiator_slots": json.dumps(initiator_free, ensure_ascii=False),
        "negotiation_blocks": json.dumps(negotiation_blocks, ensure_ascii=False, indent=2),
        "previous_reasoning": previous_reasoning or "（首轮协商，无上一轮记录）",
    })

    logger.info("[LLM Agent] NEGOTIATING 链返回: %s", json.dumps(raw, ensure_ascii=False))

    # 构建 counter_proposals（程序逻辑，不依赖 LLM）
    counter_proposals = _build_counter_proposals(
        negotiation_blocks,
        participants_info or [],
        initiator_id or "",
    )

    logger.info("counter_proposals: %d 个用户", len(counter_proposals))
    for cp in counter_proposals:
        logger.debug("  → %s: %s", cp["target_email"], cp["suggested_slots"])

    result = CoordinatorResult.model_validate({
        "decision_status": "NEGOTIATING",
        "final_time": None,
        "agent_reasoning": raw.get("agent_reasoning", "无法找到全员有空的时间段。"),
        "counter_proposals": counter_proposals,
    })
    return result.model_dump()
