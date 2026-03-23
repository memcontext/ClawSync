from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime
import json


class MeetingState(str, Enum):
    PENDING = "PENDING"
    COLLECTING = "COLLECTING"
    ANALYZING = "ANALYZING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    OVER = "OVER"           # 会议结束，通知被邀请人最终结果


class StateMachine:
    """
    会议状态机

    状态流转：
      PENDING → COLLECTING → ANALYZING → CONFIRMED → OVER
                     ↑            ↓
                     └── COLLECTING（多轮协商循环）
                                  ↓
                               FAILED → OVER（发起人取消）
                               FAILED → COLLECTING（发起人重新发起）
    """

    def __init__(self, max_rounds: int = 3):
        self.max_rounds = max_rounds
        self.transitions = {
            MeetingState.PENDING: [MeetingState.COLLECTING],
            MeetingState.COLLECTING: [MeetingState.ANALYZING, MeetingState.FAILED],
            MeetingState.ANALYZING: [MeetingState.CONFIRMED, MeetingState.COLLECTING, MeetingState.FAILED],
            MeetingState.CONFIRMED: [MeetingState.OVER],
            MeetingState.FAILED: [MeetingState.OVER, MeetingState.COLLECTING],
            MeetingState.OVER: []
        }

    def can_transition(self, current: MeetingState, target: MeetingState) -> bool:
        """检查状态转换是否合法"""
        return target in self.transitions.get(current, [])

    def transition(self, current: MeetingState, target: MeetingState,
                   context: Optional[Dict[str, Any]] = None) -> MeetingState:
        """执行状态转换"""
        if not self.can_transition(current, target):
            raise ValueError(f"无法从 {current} 转换到 {target}")

        self._before_transition(current, target, context)
        return target

    def _before_transition(self, current: MeetingState, target: MeetingState, context: Optional[Dict[str, Any]] = None):
        """转换前的业务逻辑"""
        if target == MeetingState.COLLECTING:
            if current == MeetingState.ANALYZING:
                # 多轮协商：ANALYZING → COLLECTING（重新收集）
                round_count = context.get('round_count', 0) if context else 0
                if round_count >= self.max_rounds:
                    raise ValueError("已达到最大协商轮数，无法继续协商")
                print(f"会议进入第 {round_count + 1} 轮收集")
            else:
                print(f"会议进入收集阶段: {context.get('meeting_id') if context else 'unknown'}")

        elif target == MeetingState.ANALYZING:
            print(f"会议进入分析阶段: {context.get('meeting_id') if context else 'unknown'}")

        elif target == MeetingState.CONFIRMED:
            print(f"会议协商成功: {context.get('final_time') if context else 'unknown'}")

        elif target == MeetingState.FAILED:
            print(f"会议协商失败: {context.get('reason') if context else 'unknown'}")

        elif target == MeetingState.OVER:
            print(f"会议已结束: {context.get('meeting_id') if context else 'unknown'}")
