#!/usr/bin/env python3
"""
score_meeting API
对指定会议的所有用户时间数据进行打分，输出到 meeting_score/{meeting_id}.json。

打分规则：
  - score   : 该时间段中值为 True（有空）的用户数
  - conflict: 该时间段中值为 False（明确没空）的用户 ID 列表
  - "other"（未提及）在计分和冲突中均被忽略

公开接口：
    score_meeting(meeting_id: str) -> dict
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field, RootModel

from .agent_input_format import TIME_SLOTS, _load_store

# ─── 配置 ────────────────────────────────────────────────────────────────────

SCORE_DIR = Path(__file__).resolve().parent.parent / "meeting_score"


def _score_file(meeting_id: str) -> Path:
    """返回打分结果文件路径，并确保父目录存在。"""
    SCORE_DIR.mkdir(parents=True, exist_ok=True)
    return SCORE_DIR / f"{meeting_id}.json"

# ─── Pydantic 模型 ────────────────────────────────────────────────────────────

class SlotScore(BaseModel):
    """单个时间段的打分结果。"""
    score: int = Field(description="该时间段有空的用户数（True 计数）")
    conflict: list[str] = Field(description="该时间段明确没空的用户 ID 列表（False 用户）")


class MeetingScore(RootModel[dict[str, SlotScore]]):
    """
    会议完整打分结果。
    key 为时间段（HH:MM-HH:MM），value 为 SlotScore。

    示例：
    {
        "18:00-18:30": {"score": 2, "conflict": ["user_003"]},
        "18:30-19:00": {"score": 3, "conflict": []},
        ...
    }
    """

# ─── 公开 API ─────────────────────────────────────────────────────────────────

def score_meeting(meeting_id: str) -> dict:
    """
    对指定会议的时间段打分并保存结果。

    Args:
        meeting_id: 会议唯一编号（对应 meeting_time_data/{meeting_id}.json）

    Returns:
        打分结果 dict，格式：
        {
            "18:00-18:30": {"score": 2, "conflict": ["user_003"]},
            "18:30-19:00": {"score": 3, "conflict": []},
            ...
        }

    Raises:
        FileNotFoundError: meeting_time_data/{meeting_id}.json 不存在时抛出
    """
    from .agent_input_format import DATA_DIR
    if not (DATA_DIR / f"{meeting_id}.json").exists():
        raise FileNotFoundError(
            f"找不到会议数据文件：{DATA_DIR / f'{meeting_id}.json'}"
        )

    store = _load_store(meeting_id)

    result: dict[str, SlotScore] = {}
    for slot in TIME_SLOTS:
        score = 0
        conflict: list[str] = []

        for user_id, entry in store.root.items():
            val = (entry.model_extra or {}).get(slot)
            if val is True:
                score += 1
            elif val is False:
                conflict.append(user_id)
            # val == "other" 或 None → 忽略

        result[slot] = SlotScore(score=score, conflict=conflict)

    meeting_score = MeetingScore.model_validate(result)

    path = _score_file(meeting_id)
    path.write_text(
        json.dumps(meeting_score.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return meeting_score.model_dump()
