from typing import List, Dict, Any, Optional
from datetime import datetime
import json


class LLMCoordinator:
    """LLM Coordinator - responsible for conflict resolution and time suggestions"""

    def __init__(self, model_name: str = "gpt-3.5-turbo"):
        self.model_name = model_name
        # LLM client can be initialized here

    async def analyze_availability(
            self,
            meeting_id: str,
            title: str,
            duration: int,
            participants_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Analyze all participants' available time to find common time slots
        """
        # Build prompt
        prompt = self._build_analysis_prompt(
            title=title,
            duration=duration,
            participants=participants_data
        )

        # TODO: Call LLM API for analysis
        # Using mock result for now
        result = self._mock_llm_analysis(participants_data)

        return result

    async def generate_counter_proposal(
            self,
            meeting_id: str,
            title: str,
            duration: int,
            participants_data: List[Dict[str, Any]],
            round_count: int
    ) -> Dict[str, Any]:
        """
        Generate compromise proposal suggestions
        """
        prompt = self._build_counter_proposal_prompt(
            title=title,
            duration=duration,
            participants=participants_data,
            round=round_count
        )

        # TODO: Call LLM API to generate suggestions
        # Mock return
        return {
            "proposal": "Consider Friday afternoon time slots",
            "reasoning": "Most participants are available on Friday afternoon",
            "suggested_slots": ["2026-03-20 14:00-16:00"]
        }

    def _build_analysis_prompt(self, title: str, duration: int, participants: List[Dict]) -> str:
        """Build analysis prompt"""
        prompt = f"""
        Meeting topic: {title}
        Meeting duration: {duration} minutes

        Participant availability:
        """

        for p in participants:
            prompt += f"\n- {p['email']}: {p['available_slots']}"
            if p.get('preference'):
                prompt += f" (preference: {p['preference']})"

        prompt += "\n\nPlease find all possible common available time slots and rank them by priority."

        return prompt

    def _build_counter_proposal_prompt(self, title: str, duration: int, participants: List[Dict], round: int) -> str:
        """Build counter proposal prompt"""
        prompt = f"""
        Meeting topic: {title}
        Meeting duration: {duration} minutes
        Current negotiation round: round {round}

        Current participant availability:
        """

        for p in participants:
            prompt += f"\n- {p['email']}: {p['available_slots']}"

        prompt += "\n\nNo common time slot found. Please propose a compromise, suggesting some participants adjust their time."

        return prompt

    def _mock_llm_analysis(self, participants: List[Dict]) -> Dict[str, Any]:
        """Mock LLM analysis result (for development phase)"""
        # Simple mock logic: find the first common time slot
        # In production, this should call the actual LLM API

        common_slots = self._find_common_slots(participants)

        if common_slots:
            return {
                "status": "CONFIRMED",
                "final_time": common_slots[0],
                "reasoning": "Common available time found",
                "alternative_slots": common_slots[1:3] if len(common_slots) > 1 else []
            }
        else:
            return {
                "status": "NEGOTIATING",
                "reasoning": "No common available time found, further negotiation needed",
                "suggestions": ["Consider adjusting the time range"]
            }

    def _find_common_slots(self, participants: List[Dict]) -> List[str]:
        """
        Simple common time slot finding algorithm (all slots are unified as string format)
        In production, this should be handled by the LLM
        """
        if not participants:
            return []

        per_person_slots = []
        for p in participants:
            raw_slots = p.get('available_slots', [])
            slot_set = {str(s) for s in raw_slots}
            if slot_set:
                per_person_slots.append(slot_set)

        if not per_person_slots:
            return []

        # Intersect all participants' slots
        common = per_person_slots[0]
        for s in per_person_slots[1:]:
            common = common & s

        return sorted(common) if common else []
