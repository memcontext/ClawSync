from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime
import json


class MeetingState(str, Enum):
    PENDING = "PENDING"
    COLLECTING = "COLLECTING"
    ANALYZING = "ANALYZING"
    NEGOTIATING = "NEGOTIATING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class StateMachine:
    """会议状态机"""

    def __init__(self, max_rounds: int = 3):
        self.max_rounds = max_rounds
        self.transitions = {
            MeetingState.PENDING: [MeetingState.COLLECTING],
            MeetingState.COLLECTING: [MeetingState.ANALYZING, MeetingState.NEGOTIATING],
            MeetingState.ANALYZING: [MeetingState.CONFIRMED, MeetingState.NEGOTIATING, MeetingState.FAILED],
            MeetingState.NEGOTIATING: [MeetingState.ANALYZING, MeetingState.FAILED],
            MeetingState.CONFIRMED: [],
            MeetingState.FAILED: []
        }

    def can_transition(self, current: MeetingState, target: MeetingState) -> bool:
        """检查状态转换是否合法"""
        return target in self.transitions.get(current, [])

    def transition(self, current: MeetingState, target: MeetingState,
                   context: Optional[Dict[str, Any]] = None) -> MeetingState:
        """执行状态转换"""
        if not self.can_transition(current, target):
            raise ValueError(f"无法从 {current} 转换到 {target}")

        # 执行转换前的业务逻辑
        self._before_transition(current, target, context)

        return target

    def _before_transition(self, current: MeetingState, target: MeetingState, context: Optional[Dict[str, Any]] = None):
        """转换前的业务逻辑"""
        if target == MeetingState.COLLECTING:
            # 邀请已发出，等待参与者提交
            print(f"会议进入收集阶段，等待参与者提交时间: {context.get('meeting_id') if context else 'unknown'}")

        elif target == MeetingState.ANALYZING:
            # 所有参与者已提交，准备分析
            print(f"会议进入分析阶段: {context.get('meeting_id') if context else 'unknown'}")

        elif target == MeetingState.NEGOTIATING:
            # 存在冲突，进入协商
            if context:
                round_count = context.get('round_count', 0)
                if round_count >= self.max_rounds:
                    raise ValueError("已达到最大协商轮数，无法继续协商")
                print(f"会议进入第 {round_count + 1} 轮协商")

        elif target == MeetingState.CONFIRMED:
            # 协商成功
            print(f"会议协商成功: {context.get('final_time') if context else 'unknown'}")

        elif target == MeetingState.FAILED:
            # 协商失败
            print(f"会议协商失败: {context.get('reason') if context else 'unknown'}")