#!/usr/bin/env python3
"""
score_meeting API
对指定会议的所有用户时间数据进行打分，输出到 meeting_score/{meeting_id}.json。

时间槽 key 格式：YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM
动态收集所有用户提到的时间槽，不依赖固定列表。

打分规则：
  - score   : 该时间段中值为 True（有空）的用户数
  - conflict: 该时间段中值为 False（明确没空）的用户 ID 列表
  - 用户未提及该时间段 → 忽略（不计入 score 也不计入 conflict）

公开接口：
    score_meeting(meeting_id: str) -> dict
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field, RootModel

from .agent_input_format import _load_store

# ─── 配置 ────────────────────────────────────────────────────────────────────

SCORE_DIR = Path(__file__).resolve().parent.parent / "meeting_score"


def _score_file(meeting_id: str) -> Path:
    SCORE_DIR.mkdir(parents=True, exist_ok=True)
    return SCORE_DIR / f"{meeting_id}.json"

# ─── Pydantic 模型 ────────────────────────────────────────────────────────────

class SlotScore(BaseModel):
    score: int = Field(description="该时间段有空的用户数（True 计数）")
    conflict: list[str] = Field(description="该时间段明确没空的用户 ID 列表（False 用户）")


class MeetingScore(RootModel[dict[str, SlotScore]]):
    """
    会议完整打分结果。
    key 为日期感知时间段（YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM），value 为 SlotScore。
    """

# ─── 公开 API ─────────────────────────────────────────────────────────────────

def score_meeting(meeting_id: str) -> dict:
    """
    对指定会议的时间段打分并保存结果。

    动态收集所有用户提到的时间槽 key，逐槽统计：
      - True  → score +1
      - False → 加入 conflict 列表
      - 不存在（用户未提及）→ 忽略

    Returns:
        {
            "2026-03-21 10:00--2026-03-21 10:30": {"score": 2, "conflict": []},
            "2026-03-21 10:30--2026-03-21 11:00": {"score": 1, "conflict": ["2"]},
            ...
        }
    """
    from .agent_input_format import DATA_DIR
    if not (DATA_DIR / f"{meeting_id}.json").exists():
        raise FileNotFoundError(
            f"找不到会议数据文件：{DATA_DIR / f'{meeting_id}.json'}"
        )

    store = _load_store(meeting_id)

    # ── 第一步：动态收集所有用户提到的时间槽 key ───────────────────────────────
    all_slots: set[str] = set()
    for user_id, entry in store.root.items():
        for key in (entry.model_extra or {}):
            # 只收集 slot key（含 "--" 的），排除 user_ID / meeting_ID 等元数据
            if "--" in key:
                all_slots.add(key)

    # 按时间排序（字符串排序即可，因为格式统一）
    sorted_slots = sorted(all_slots)

    # ── 第二步：逐槽打分 ─────────────────────────────────────────────────────
    # 对于标准格式用户（只存 True 的槽），未出现的槽视为不可用（conflict）
    result: dict[str, SlotScore] = {}
    for slot in sorted_slots:
        score = 0
        conflict: list[str] = []

        for user_id, entry in store.root.items():
            extras = entry.model_extra or {}
            val = extras.get(slot)
            if val is True:
                score += 1
            elif val is False:
                conflict.append(user_id)
            else:
                # 用户未提及该槽：如果该用户有其他槽数据，说明是标准格式，
                # 未出现的槽 = 不可用 → 计入 conflict
                has_any_slot = any("--" in k for k in extras)
                if has_any_slot:
                    conflict.append(user_id)
                # 否则该用户完全无数据，忽略

        result[slot] = SlotScore(score=score, conflict=conflict)

    meeting_score = MeetingScore.model_validate(result)

    path = _score_file(meeting_id)
    path.write_text(
        json.dumps(meeting_score.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return meeting_score.model_dump()
