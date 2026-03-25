from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from enum import Enum


class UserCreate(BaseModel):
    email: EmailStr


class SendCodeRequest(BaseModel):
    email: EmailStr


class VerifyBindRequest(BaseModel):
    email: EmailStr
    code: str


class ResponseType(str, Enum):
    """提交响应类型"""
    INITIAL = "INITIAL"
    ACCEPT_PROPOSAL = "ACCEPT_PROPOSAL"
    NEW_PROPOSAL = "NEW_PROPOSAL"
    COUNTER = "COUNTER"             # 插件兼容别名，等同于 NEW_PROPOSAL
    REJECT = "REJECT"


class InitiatorData(BaseModel):
    """发起人数据：空闲时间 + 偏好说明"""
    available_slots: List[str]  # 格式: ["2026-03-18 14:00-18:00"]
    preference_note: Optional[str] = None


class MeetingCreate(BaseModel):
    """
    创建会议请求体 — 与设计文档对齐:
    {
        "title": "...",
        "duration_minutes": 30,
        "invitees": ["b@x.com"],
        "initiator_data": {
            "available_slots": ["2026-03-18 14:00-18:00"],
            "preference_note": "尽量安排在下午"
        }
    }
    """
    title: str
    duration_minutes: int
    invitees: List[EmailStr]
    initiator_data: InitiatorData


class SubmitAvailabilityRequest(BaseModel):
    """
    提交空闲时间请求体 — 统一为字符串格式:
    {
        "response_type": "INITIAL",
        "available_slots": ["2026-03-18 15:00-17:00"],
        "preference_note": "...",
        "duration_minutes": 60,
        "invitees": ["new@example.com"]
    }
    duration_minutes 和 invitees 仅在 FAILED 状态下发起人重新发起时可用，
    用于修改会议参数后开始新一轮协商。
    """
    response_type: ResponseType = ResponseType.INITIAL
    available_slots: List[str] = []  # 格式统一: ["2026-03-18 15:00-17:00"]
    preference_note: Optional[str] = None
    duration_minutes: Optional[int] = None  # FAILED 重新发起时可修改时长
    invitees: Optional[List[EmailStr]] = None  # FAILED 重新发起时可修改参与者


class CounterProposalItem(BaseModel):
    """
    Coordinator Agent 下发的妥协建议（针对某个参与者）
    {
        "target_email": "alice@example.com",
        "message": "Bob只有下午有空，建议您调整到以下时间段",
        "suggested_slots": ["2026-03-18 17:00-18:00", "2026-03-19 14:00-16:00"]
    }
    """
    target_email: str
    message: str
    suggested_slots: List[str] = []  # Agent 建议的时间槽


class DecisionStatus(str, Enum):
    """Agent 决策状态"""
    CONFIRMED = "CONFIRMED"
    NEGOTIATING = "NEGOTIATING"
    FAILED = "FAILED"


class AgentCoordinationResult(BaseModel):
    """
    Coordinator Agent 提交的协调决策结果:
    {
        "decision_status": "CONFIRMED",
        "final_time": "2026-03-18 15:00-15:30",
        "agent_reasoning": "...",
        "counter_proposals": []
    }
    """
    decision_status: DecisionStatus
    final_time: Optional[str] = None
    agent_reasoning: str
    counter_proposals: List[CounterProposalItem] = []


class APIResponse(BaseModel):
    code: int = 200
    message: str = ""
    data: Optional[Any] = None

    class Config:
        from_attributes = True
