#!/usr/bin/env python3
"""
user_time_format / submit_user_time API
将用户时间描述转换为结构化 JSON，支持两种输入格式：
  1. 标准 API 格式：available_slots 列表，每项含 start/end（YYYY-MM-DD HH:MM）
  2. 自然语言：由 LLM Agent 解析

时间槽 key 格式：YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM（双横线分隔）
只存储用户实际提到的时间槽（True / False），不再填充固定 36 个槽。

每个 meeting_id 对应 meeting_time_data/{meeting_id}.json，同一会议的多个用户共享同一文件。

公开接口：
    user_time_format(user_input, user_id, meeting_id, reference_date) -> dict
    submit_user_time(user_input, user_id, meeting_id, reference_date) -> dict
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from typing import Literal, Union
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from config import DOUBAO_API_KEY, DOUBAO_BASE_URL, DOUBAO_MODEL, LLM_TEMPERATURE
from utils.logger import get_logger

logger = get_logger("agent_input_format")

# ─── 配置 ────────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).resolve().parent.parent / "meeting_time_data"

# 日期感知 slot key 正则：YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM
_DATED_SLOT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}--\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"
)

# 旧格式兼容（LLM 输出用）：HH:MM-HH:MM
_SLOT_RE = re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")


def _meeting_file(meeting_id: str) -> Path:
    """返回指定会议的 JSON 文件路径，并确保父目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{meeting_id}.json"


def _gen_slots() -> list[str]:
    """生成 06:00–00:00（次日）每 30 分钟一个时间段（HH:MM-HH:MM 格式，仅 LLM 参考用）。"""
    slots = []
    for h in range(6, 24):
        for m in (0, 30):
            end_total = h * 60 + m + 30
            end_h, end_m = divmod(end_total % (24 * 60), 60)
            slots.append(f"{h:02d}:{m:02d}-{end_h:02d}:{end_m:02d}")
    return slots


# LLM 自然语言解析时使用的参考时间段列表
TIME_SLOTS: list[str] = _gen_slots()

# ─── Pydantic 模型 ────────────────────────────────────────────────────────────

SlotValue = Union[bool, Literal["other"]]


class AvailabilityOutput(BaseModel):
    """LLM 输出结构：HH:MM-HH:MM → True/False/"other" 映射（自然语言解析用）。"""

    slots: dict[str, SlotValue] = Field(
        description=(
            "所有时间段的可用性映射。"
            "key 格式必须为 'HH:MM-HH:MM'（如 '18:00-18:30'），"
            "value 只能是三种值之一：\n"
            "  true   —— 用户明确表示该时间段有空\n"
            "  false  —— 用户明确表示该时间段没空\n"
            "  \"other\" —— 用户未提及该时间段"
        )
    )

    @field_validator("slots")
    @classmethod
    def check_key_format(cls, v: dict[str, SlotValue]) -> dict[str, SlotValue]:
        bad_keys = [k for k in v if not _SLOT_RE.match(k)]
        if bad_keys:
            raise ValueError(f"时间段 key 格式错误（应为 HH:MM-HH:MM）：{bad_keys}")
        bad_vals = [k for k, val in v.items() if not isinstance(val, bool) and val != "other"]
        if bad_vals:
            raise ValueError(f"值非法（只允许 true/false/\"other\"），以下字段有问题：{bad_vals}")
        return v

    @model_validator(mode="after")
    def check_all_slots_present(self) -> "AvailabilityOutput":
        missing = [s for s in TIME_SLOTS if s not in self.slots]
        if missing:
            raise ValueError(
                f"输出缺少 {len(missing)} 个时间段，"
                f"例如：{missing[:3]}{'...' if len(missing) > 3 else ''}"
            )
        return self


class RoleEntry(BaseModel):
    """
    单个用户在某次会议中的完整条目。

    固定元数据字段：user_ID、meeting_ID
    动态时间槽字段：以 extra="allow" 方式附加
      key 格式为 YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM
    """

    model_config = ConfigDict(extra="allow")

    user_ID: str = Field(description="用户唯一标识")
    meeting_ID: str = Field(description="会议唯一编号")

    @model_validator(mode="after")
    def check_extra_keys(self) -> "RoleEntry":
        bad = [
            k for k in (self.model_extra or {})
            if not _DATED_SLOT_RE.match(k)
        ]
        if bad:
            raise ValueError(f"RoleEntry 中存在非法 key（应为 YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM）：{bad}")
        return self

    def get_slots(self) -> dict[str, SlotValue]:
        return dict(self.model_extra or {})


class AvailabilityStore(RootModel[dict[str, RoleEntry]]):
    """单个会议 JSON 文件的完整存储结构。"""

# ─── 数据层 ───────────────────────────────────────────────────────────────────

def _load_store(meeting_id: str) -> AvailabilityStore:
    path = _meeting_file(meeting_id)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return AvailabilityStore.model_validate(raw)
    return AvailabilityStore.model_validate({})


def _save_store(store: AvailabilityStore, meeting_id: str) -> None:
    path = _meeting_file(meeting_id)
    path.write_text(
        json.dumps(store.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

# ─── 标准 API 格式解析 ────────────────────────────────────────────────────────

_STANDARD_SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def _is_standard_slots(user_input: object) -> bool:
    """判断是否为标准 API 格式：list[dict] 且每项含 start/end（YYYY-MM-DD HH:MM）。"""
    if not isinstance(user_input, list) or len(user_input) == 0:
        return False
    first = user_input[0]
    return (
        isinstance(first, dict)
        and "start" in first
        and "end" in first
        and _STANDARD_SLOT_RE.match(str(first.get("start", "")))
    )


def _parse_standard_slots(available_slots: list[dict]) -> dict[str, bool]:
    """
    将标准 API 格式的时间段列表转换为日期感知的 30 分钟时间槽。
    只生成 True 的槽（用户有空的时间）。

    Args:
        available_slots: [{"start": "2026-03-21 10:00", "end": "2026-03-21 12:00"}, ...]

    Returns:
        {"2026-03-21 10:00--2026-03-21 10:30": True, "2026-03-21 10:30--2026-03-21 11:00": True, ...}
    """
    result: dict[str, bool] = {}

    for item in available_slots:
        start = datetime.strptime(str(item["start"]), "%Y-%m-%d %H:%M")
        end = datetime.strptime(str(item["end"]), "%Y-%m-%d %H:%M")

        current = start
        while current + timedelta(minutes=30) <= end:
            next_time = current + timedelta(minutes=30)
            key = f"{current.strftime('%Y-%m-%d %H:%M')}--{next_time.strftime('%Y-%m-%d %H:%M')}"
            result[key] = True
            current = next_time

    logger.debug("标准格式解析: %d 个区间 → %d 个 True 槽", len(available_slots), len(result))
    return result


# ─── LangChain 链（自然语言解析）──────────────────────────────────────────────

def _build_chain():
    llm = ChatOpenAI(
        model=DOUBAO_MODEL,
        api_key=DOUBAO_API_KEY,
        base_url=DOUBAO_BASE_URL,
        temperature=LLM_TEMPERATURE,
    )
    structured_llm = llm.with_structured_output(AvailabilityOutput)

    slots_text = "\n".join(f"  {s}" for s in TIME_SLOTS)

    system_prompt = f"""你是一个时间安排助手，负责将用户的自然语言描述转换为时间可用性数据。

可用时间段列表（24 小时制，每段 30 分钟，06:00 至次日 00:00）：
{slots_text}

任务：根据用户输入，为 slots 中的每个时间段填写三态值，输出符合要求的 json 格式。

规则：
1. 所有时间段都必须出现在 slots 中，不可遗漏
2. 用户明确提到"有空/可以/有时间"的时间段 → true
3. 用户明确提到"没空/忙/不行/有事"的时间段 → false
4. 用户未提及的时间段 → "other"
5. "今晚" → 18:00 之后；"上午" → 06:00–12:00；"下午" → 12:00–18:00
6. 时间以 30 分钟为最小粒度"""

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{user_input}"),
    ])

    return prompt | structured_llm


def _convert_llm_slots_to_dated(
    llm_slots: dict[str, SlotValue],
    reference_date: str,
) -> dict[str, bool]:
    """
    将 LLM 输出的 HH:MM-HH:MM 槽转换为日期感知格式。
    只保留 True 和 False 的槽，丢弃 "other"。

    Args:
        llm_slots: {"18:00-18:30": True, "18:30-19:00": False, "19:00-19:30": "other", ...}
        reference_date: "2026-03-21"

    Returns:
        {"2026-03-21 18:00--2026-03-21 18:30": True, "2026-03-21 18:30--2026-03-21 19:00": False, ...}
    """
    result: dict[str, bool] = {}
    for slot_key, val in llm_slots.items():
        if val == "other":
            continue
        parts = slot_key.split("-")
        start_hm = parts[0]
        end_hm = parts[1]
        key = f"{reference_date} {start_hm}--{reference_date} {end_hm}"
        result[key] = val
    return result


# ─── 公开 API ─────────────────────────────────────────────────────────────────

def user_time_format(
    user_input: str,
    user_id: str,
    meeting_id: str,
    reference_date: str | None = None,
) -> dict:
    """
    将用户自然语言时间描述转换为结构化可用性数据并持久化。

    Args:
        user_input:     自然语言时间描述
        user_id:        用户唯一标识
        meeting_id:     会议唯一编号
        reference_date: 参考日期 "YYYY-MM-DD"，用于生成日期感知的 slot key

    Returns:
        该用户的完整条目 dict
    """
    logger.info("[LLM Agent] 自然语言解析 user_id=%s, meeting=%s, input=%r", user_id, meeting_id, user_input)
    output: AvailabilityOutput = _build_chain().invoke({"user_input": user_input})
    true_count = sum(1 for v in output.slots.values() if v is True)
    false_count = sum(1 for v in output.slots.values() if v is False)
    logger.info("[LLM Agent] 解析结果: True=%d, False=%d, other=%d", true_count, false_count, len(output.slots) - true_count - false_count)

    if reference_date:
        slots = _convert_llm_slots_to_dated(output.slots, reference_date)
    else:
        slots = {k: v for k, v in output.slots.items() if v != "other"}

    entry = RoleEntry(user_ID=user_id, meeting_ID=meeting_id, **slots)
    store = _load_store(meeting_id)
    store.root[user_id] = entry
    _save_store(store, meeting_id)
    logger.debug("已保存 user_id=%s 到 meeting_time_data/%s.json, 槽数=%d", user_id, meeting_id, len(slots))
    return entry.model_dump()


def submit_user_time(
    user_input: "str | list[dict]",
    user_id: str,
    meeting_id: str,
    reference_date: str | None = None,
) -> dict:
    """
    统一入口：自动识别输入格式并存储用户时间数据。

    Args:
        user_input:     list[dict] 标准 API 格式 或 str 自然语言
        user_id:        用户唯一标识
        meeting_id:     会议唯一编号
        reference_date: 参考日期（自然语言解析时用）

    Returns:
        该用户的完整条目 dict
    """
    if _is_standard_slots(user_input):
        logger.info("submit_user_time: user=%s, meeting=%s, 标准格式 (%d 个区间)", user_id, meeting_id, len(user_input))
        slots = _parse_standard_slots(user_input)  # type: ignore[arg-type]
    else:
        logger.info("[LLM Agent] submit_user_time: user=%s, meeting=%s, 自然语言=%r", user_id, meeting_id, user_input)
        output: AvailabilityOutput = _build_chain().invoke(
            {"user_input": str(user_input)}
        )
        true_count = sum(1 for v in output.slots.values() if v is True)
        false_count = sum(1 for v in output.slots.values() if v is False)
        logger.info("[LLM Agent] 解析结果: True=%d, False=%d, other=%d", true_count, false_count, len(output.slots) - true_count - false_count)
        if reference_date:
            slots = _convert_llm_slots_to_dated(output.slots, reference_date)
        else:
            slots = {k: v for k, v in output.slots.items() if v != "other"}

    entry = RoleEntry(user_ID=user_id, meeting_ID=meeting_id, **slots)
    store = _load_store(meeting_id)
    store.root[user_id] = entry
    _save_store(store, meeting_id)
    logger.debug("已保存 user=%s → meeting_time_data/%s.json, 槽数=%d", user_id, meeting_id, len(slots))
    return entry.model_dump()
