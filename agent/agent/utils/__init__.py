from .input_handle import coordinate_from_task, coordinate_meeting, handle_meeting
from .output_summary import summarize_meeting
from .agent_input_format import submit_user_time, user_time_format
from .scoring import score_meeting

__all__ = [
    "coordinate_from_task", # 一站式入口，直接接收 API 7 task（推荐）
    "coordinate_meeting",   # 一站式入口，手动构造 role_inputs
    "handle_meeting",       # 仅收集+打分
    "summarize_meeting",    # 仅 LLM 推荐（返回 API 8 格式）
    "submit_user_time",     # 单用户时间提交
    "user_time_format",     # 单用户自然语言解析
    "score_meeting",        # 打分
]
