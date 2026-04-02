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
    OVER = "OVER"           # Meeting ended, notify invitees of final result


class StateMachine:
    """
    Meeting state machine

    State transitions:
      PENDING -> COLLECTING -> ANALYZING -> CONFIRMED -> OVER
                     ^            |
                     +-- COLLECTING (multi-round negotiation loop)
                                  |
                               FAILED -> OVER (initiator cancels)
                               FAILED -> COLLECTING (initiator re-initiates)
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
        """Check if the state transition is valid"""
        return target in self.transitions.get(current, [])

    def transition(self, current: MeetingState, target: MeetingState,
                   context: Optional[Dict[str, Any]] = None) -> MeetingState:
        """Execute state transition"""
        if not self.can_transition(current, target):
            raise ValueError(f"Cannot transition from {current} to {target}")

        self._before_transition(current, target, context)
        return target

    def _before_transition(self, current: MeetingState, target: MeetingState, context: Optional[Dict[str, Any]] = None):
        """Pre-transition business logic"""
        if target == MeetingState.COLLECTING:
            if current == MeetingState.ANALYZING:
                # Multi-round negotiation: ANALYZING -> COLLECTING (re-collect)
                round_count = context.get('round_count', 0) if context else 0
                if round_count >= self.max_rounds:
                    raise ValueError("Maximum negotiation rounds reached, cannot continue negotiation")
                print(f"Meeting entering collection round {round_count + 1}")
            else:
                print(f"Meeting entering collection phase: {context.get('meeting_id') if context else 'unknown'}")

        elif target == MeetingState.ANALYZING:
            print(f"Meeting entering analysis phase: {context.get('meeting_id') if context else 'unknown'}")

        elif target == MeetingState.CONFIRMED:
            print(f"Meeting negotiation successful: {context.get('final_time') if context else 'unknown'}")

        elif target == MeetingState.FAILED:
            print(f"Meeting negotiation failed: {context.get('reason') if context else 'unknown'}")

        elif target == MeetingState.OVER:
            print(f"Meeting ended: {context.get('meeting_id') if context else 'unknown'}")
