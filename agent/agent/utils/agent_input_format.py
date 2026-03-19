#!/usr/bin/env python3
"""
user_time_format / submit_user_time API
将用户时间描述转换为结构化 JSON，支持两种输入格式：
  1. 标准 API 格式：available_slots 列表，每项含 start/end（YYYY-MM-DD HH:MM）
  2. 自然语言：由 LLM Agent 解析

每个 meeting_id 对应 meeting_time_data/{meeting_id}.json，同一会议的多个用户共享同一文件。

公开接口：
    user_time_format(user_input, user_id, meeting_id) -> dict   # 仅自然语言
    submit_user_time(user_input, user_id, meeting_id) -> dict   # 自动识别格式
"""

import json
import os
import re
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from typing import Literal, Union
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

# ─── 配置 ────────────────────────────────────────────────────────────────────

# 所有会议 JSON 文件统一存放于项目根目录的 meeting_time_data 文件夹
DATA_DIR = Path(__file__).resolve().parent.parent / "meeting_time_data"
_SLOT_RE = re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")


def _meeting_file(meeting_id: str) -> Path:
    """返回指定会议的 JSON 文件路径，并确保父目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{meeting_id}.json"


def _gen_slots() -> list[str]:
    """生成 06:00–00:00（次日）每 30 分钟一个时间段。"""
    slots = []
    for h in range(6, 24):
        for m in (0, 30):
            end_total = h * 60 + m + 30
            end_h, end_m = divmod(end_total % (24 * 60), 60)
            slots.append(f"{h:02d}:{m:02d}-{end_h:02d}:{end_m:02d}")
    return slots


TIME_SLOTS: list[str] = _gen_slots()

# ─── Pydantic 模型 ────────────────────────────────────────────────────────────

# 时间槽的三态值类型：有空 / 没空 / 未提及
SlotValue = Union[bool, Literal["other"]]


class AvailabilityOutput(BaseModel):
    """
    LLM 输出结构：纯时间槽映射。
    由 with_structured_output 填充，经过两层 Pydantic 校验后方可使用。
    """

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
        """每个 key 必须匹配 HH:MM-HH:MM 格式；value 只能是 true/false/'other'。"""
        bad_keys = [k for k in v if not _SLOT_RE.match(k)]
        if bad_keys:
            raise ValueError(f"时间段 key 格式错误（应为 HH:MM-HH:MM）：{bad_keys}")
        bad_vals = [k for k, val in v.items() if not isinstance(val, bool) and val != "other"]
        if bad_vals:
            raise ValueError(f"值非法（只允许 true/false/\"other\"），以下字段有问题：{bad_vals}")
        return v

    @model_validator(mode="after")
    def check_all_slots_present(self) -> "AvailabilityOutput":
        """所有预定义时间段必须全部出现，不允许遗漏。"""
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
    动态时间槽字段：以 extra="allow" 方式附加，key 格式为 HH:MM-HH:MM

    序列化示例：
    {
        "user_ID": "user_001",
        "meeting_ID": "meeting_12345",
        "18:00-18:30": true,
        "18:30-19:00": true,
        ...
    }
    """

    model_config = ConfigDict(extra="allow")

    user_ID: str = Field(description="用户唯一标识")
    meeting_ID: str = Field(description="会议唯一编号")

    @model_validator(mode="after")
    def check_extra_keys(self) -> "RoleEntry":
        """额外字段只能是合法的时间槽 key。"""
        bad = [k for k in (self.model_extra or {}) if not _SLOT_RE.match(k)]
        if bad:
            raise ValueError(f"RoleEntry 中存在非法 key：{bad}")
        return self

    def get_slots(self) -> dict[str, SlotValue]:
        """返回时间槽部分（排除元数据字段）。"""
        return dict(self.model_extra or {})


class AvailabilityStore(RootModel[dict[str, RoleEntry]]):
    """
    单个会议 JSON 文件的完整存储结构。
    外层 key 为用户标识（如 "user_001"），value 为该用户的 RoleEntry。

    对应文件：meeting_time_data/{meeting_id}.json
    {
        "user_001": { "user_ID": "user_001", "meeting_ID": "m1", "18:00-18:30": true, ... },
        "user_002": { "user_ID": "user_002", "meeting_ID": "m1", "09:00-09:30": true, ... }
    }
    """

# ─── 数据层 ───────────────────────────────────────────────────────────────────

def _load_store(meeting_id: str) -> AvailabilityStore:
    """加载指定会议的 JSON 文件；文件不存在则返回空 store。"""
    path = _meeting_file(meeting_id)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return AvailabilityStore.model_validate(raw)
    return AvailabilityStore.model_validate({})


def _save_store(store: AvailabilityStore, meeting_id: str) -> None:
    """将 store 写入对应会议的 JSON 文件。"""
    path = _meeting_file(meeting_id)
    path.write_text(
        json.dumps(store.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

# ─── LangChain 链 ─────────────────────────────────────────────────────────────

def _build_chain():
    """
    构建 LangChain 链：
      ChatPromptTemplate → ChatAnthropic.with_structured_output(AvailabilityOutput)
    """
    # 豆包 Pro 32K，通过火山方舟 OpenAI 兼容接口接入
    # TODO: 生产环境请将 API Key 移至环境变量，避免泄露
    llm = ChatOpenAI(
        model="doubao-1-5-pro-32k-250115",
        api_key="c4d34f89-32e8-4c59-ad87-2029e083c307",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        temperature=0,
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

# ─── 公开 API ─────────────────────────────────────────────────────────────────

def user_time_format(user_input: str, user_id: str, meeting_id: str) -> dict:
    """
    将用户自然语言时间描述转换为结构化可用性数据并持久化。

    Args:
        user_input:  用户的自然语言时间描述，例如"我今晚 6 点到 7 点半有空"
        user_id:     用户唯一标识，作为 JSON 顶层 key 及 user_ID 字段的值
        meeting_id:  会议唯一编号，写入 meeting_ID 字段

    Returns:
        该用户的完整条目（dict），格式如下：
        {
            "user_ID": "user_001",
            "meeting_ID": "meeting_12345",
            "06:00-06:30": false,
            ...
            "18:00-18:30": true,
            "18:30-19:00": true,
            "19:00-19:30": true,
            ...
        }

    Raises:
        pydantic.ValidationError: LLM 输出不符合 schema 时抛出
    """
    # 1. LLM 解析 → AvailabilityOutput（经 Pydantic 校验）
    output: AvailabilityOutput = _build_chain().invoke({"user_input": user_input})

    # 2. 组装 RoleEntry（user_ID + meeting_ID + 时间槽）
    entry = RoleEntry(user_ID=user_id, meeting_ID=meeting_id, **output.slots)

    # 3. 加载该会议的 store，写入 / 覆盖该用户条目，保存
    store = _load_store(meeting_id)
    store.root[user_id] = entry
    _save_store(store, meeting_id)

    return entry.model_dump()

# ─── 标准 API 格式解析 ────────────────────────────────────────────────────────

_STANDARD_SLOT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"
)


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


def _parse_standard_slots(
    available_slots: list[dict],
) -> dict[str, SlotValue]:
    """
    将标准 API 格式的时间段列表转换为时间槽映射。

    只取 HH:MM 部分（暂时忽略日期，后续再改）。
    完全包含在某个可用区间内的时间槽 → True，其余 → "other"。

    Args:
        available_slots: [{"start": "YYYY-MM-DD HH:MM", "end": "YYYY-MM-DD HH:MM"}, ...]
    """
    result: dict[str, SlotValue] = {slot: "other" for slot in TIME_SLOTS}

    # 将每个区间转换为分钟数（仅 HH:MM 部分）
    ranges: list[tuple[int, int]] = []
    for item in available_slots:
        start_hm = str(item["start"]).split(" ")[-1]  # "HH:MM"
        end_hm   = str(item["end"]).split(" ")[-1]
        sh, sm = map(int, start_hm.split(":"))
        eh, em = map(int, end_hm.split(":"))
        ranges.append((sh * 60 + sm, eh * 60 + em))

    for slot in TIME_SLOTS:
        slot_start_str, slot_end_str = slot.split("-")
        ss_h, ss_m = map(int, slot_start_str.split(":"))
        slot_start_mins = ss_h * 60 + ss_m
        slot_end_mins   = slot_start_mins + 30

        for (range_start, range_end) in ranges:
            if slot_start_mins >= range_start and slot_end_mins <= range_end:
                result[slot] = True
                break

    return result


def submit_user_time(
    user_input: "str | list[dict]",
    user_id: str,
    meeting_id: str,
) -> dict:
    """
    统一入口：自动识别输入格式并存储用户时间数据。

    Args:
        user_input: 两种格式之一
            - list[dict]：标准 API 格式，每项含 start/end（YYYY-MM-DD HH:MM）
            - str：自然语言描述，由 LLM 解析
        user_id:    用户唯一标识
        meeting_id: 会议唯一编号

    Returns:
        该用户的完整条目 dict（同 user_time_format）
    """
    if _is_standard_slots(user_input):
        slots = _parse_standard_slots(user_input)  # type: ignore[arg-type]
    else:
        output: AvailabilityOutput = _build_chain().invoke(
            {"user_input": str(user_input)}
        )
        slots = output.slots

    entry = RoleEntry(user_ID=user_id, meeting_ID=meeting_id, **slots)
    store = _load_store(meeting_id)
    store.root[user_id] = entry
    _save_store(store, meeting_id)
    return entry.model_dump()


# ─── 命令行演示入口 ────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  user_time_format  CLI 演示")
    print("=" * 55)
    print("示例输入：我在今晚 6:00 到 7:30 有空，其他时间没空")
    print("输入 quit 退出\n")

    default_user_id    = "user_001"
    default_meeting_id = "meeting_12345"

    while True:
        try:
            user_input = input("请输入时间描述：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "退出", "q"):
            print("再见！")
            break

        try:
            result = user_time_format(user_input, default_user_id, default_meeting_id)

            free = [k for k in TIME_SLOTS if result.get(k)]
            print(f"\n📅 [{result['user_ID']} / {result['meeting_ID']}] 有空时间段：")
            if free:
                for s in free:
                    print(f"  ✓ {s}")
            else:
                print("  （暂无有空时间段）")
            saved_path = _meeting_file(default_meeting_id)
            print(f"✅ 已保存至 {saved_path}\n")

        except Exception as e:
            print(f"❌ 出错：{e}\n")


if __name__ == "__main__":
    main()
