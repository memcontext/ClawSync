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
    """Submission response type"""
    INITIAL = "INITIAL"
    ACCEPT_PROPOSAL = "ACCEPT_PROPOSAL"
    NEW_PROPOSAL = "NEW_PROPOSAL"
    COUNTER = "COUNTER"             # Plugin compatibility alias, equivalent to NEW_PROPOSAL
    REJECT = "REJECT"


class InitiatorData(BaseModel):
    """Initiator data: available time slots + preference note"""
    available_slots: List[str]  # Format: ["2026-03-18 14:00-18:00"]
    preference_note: Optional[str] = None


class MeetingCreate(BaseModel):
    """
    Create meeting request body -- aligned with design document:
    {
        "title": "...",
        "duration_minutes": 30,
        "invitees": ["b@x.com"],
        "initiator_data": {
            "available_slots": ["2026-03-18 14:00-18:00"],
            "preference_note": "preferably in the afternoon"
        }
    }
    """
    title: str
    duration_minutes: int
    invitees: List[EmailStr]
    initiator_data: InitiatorData


class SubmitAvailabilityRequest(BaseModel):
    """
    Submit availability request body -- unified string format:
    {
        "response_type": "INITIAL",
        "available_slots": ["2026-03-18 15:00-17:00"],
        "preference_note": "...",
        "duration_minutes": 60,
        "invitees": ["new@example.com"]
    }
    duration_minutes and invitees are only available when the initiator re-initiates from FAILED status,
    used to modify meeting parameters before starting a new round of negotiation.
    """
    response_type: ResponseType = ResponseType.INITIAL
    available_slots: List[str] = []  # Unified format: ["2026-03-18 15:00-17:00"]
    preference_note: Optional[str] = None
    duration_minutes: Optional[int] = None  # Can modify duration when re-initiating from FAILED
    invitees: Optional[List[EmailStr]] = None  # Can modify participants when re-initiating from FAILED


class CounterProposalItem(BaseModel):
    """
    Coordinator Agent's compromise suggestion (for a specific participant)
    {
        "target_email": "alice@example.com",
        "message": "Bob is only available in the afternoon, suggest you adjust to the following time slots",
        "suggested_slots": ["2026-03-18 17:00-18:00", "2026-03-19 14:00-16:00"]
    }
    """
    target_email: str
    message: str
    suggested_slots: List[str] = []  # Agent's suggested time slots


class DecisionStatus(str, Enum):
    """Agent decision status"""
    CONFIRMED = "CONFIRMED"
    NEGOTIATING = "NEGOTIATING"
    FAILED = "FAILED"


class AgentCoordinationResult(BaseModel):
    """
    Coordination decision result submitted by the Coordinator Agent:
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
