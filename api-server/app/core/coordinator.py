from typing import List, Dict, Any, Optional
from datetime import datetime
import json


class LLMCoordinator:
    """LLM协调器 - 负责冲突消解和时间建议"""

    def __init__(self, model_name: str = "gpt-3.5-turbo"):
        self.model_name = model_name
        # 这里可以初始化LLM客户端

    async def analyze_availability(
            self,
            meeting_id: str,
            title: str,
            duration: int,
            participants_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        分析所有参与者的空闲时间，寻找共同时间段
        """
        # 构建提示词
        prompt = self._build_analysis_prompt(
            title=title,
            duration=duration,
            participants=participants_data
        )

        # TODO: 调用LLM API进行分析
        # 这里先模拟返回结果
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
        生成妥协方案建议
        """
        prompt = self._build_counter_proposal_prompt(
            title=title,
            duration=duration,
            participants=participants_data,
            round=round_count
        )

        # TODO: 调用LLM API生成建议
        # 模拟返回
        return {
            "proposal": "建议考虑周五下午的时间段",
            "reasoning": "多数参与者周五下午有空",
            "suggested_slots": ["2026-03-20 14:00-16:00"]
        }

    def _build_analysis_prompt(self, title: str, duration: int, participants: List[Dict]) -> str:
        """构建分析提示词"""
        prompt = f"""
        会议主题: {title}
        会议时长: {duration}分钟

        参与者空闲时间:
        """

        for p in participants:
            prompt += f"\n- {p['email']}: {p['available_slots']}"
            if p.get('preference'):
                prompt += f" (偏好: {p['preference']})"

        prompt += "\n\n请找出所有可能的共同空闲时间段，并按优先级排序。"

        return prompt

    def _build_counter_proposal_prompt(self, title: str, duration: int, participants: List[Dict], round: int) -> str:
        """构建妥协建议提示词"""
        prompt = f"""
        会议主题: {title}
        会议时长: {duration}分钟
        当前协商轮次: 第{round}轮

        当前参与者时间:
        """

        for p in participants:
            prompt += f"\n- {p['email']}: {p['available_slots']}"

        prompt += "\n\n由于无法找到共同时间，请提出妥协方案，建议部分参与者调整时间。"

        return prompt

    def _mock_llm_analysis(self, participants: List[Dict]) -> Dict[str, Any]:
        """模拟LLM分析结果（开发阶段使用）"""
        # 简单的模拟逻辑：寻找第一个共同时间段
        # 实际项目中应该调用真实的LLM API

        common_slots = self._find_common_slots(participants)

        if common_slots:
            return {
                "status": "CONFIRMED",
                "final_time": common_slots[0],
                "reasoning": "找到共同空闲时间",
                "alternative_slots": common_slots[1:3] if len(common_slots) > 1 else []
            }
        else:
            return {
                "status": "NEGOTIATING",
                "reasoning": "未找到共同空闲时间，需要进一步协商",
                "suggestions": ["建议考虑调整时间范围"]
            }

    def _find_common_slots(self, participants: List[Dict]) -> List[str]:
        """
        简单的共同时间段查找算法（所有 slots 已统一为字符串格式）
        实际项目中应由LLM处理
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

        # 取所有参与者的交集
        common = per_person_slots[0]
        for s in per_person_slots[1:]:
            common = common & s

        return sorted(common) if common else []