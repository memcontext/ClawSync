from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from ..models.database import User, Meeting, NegotiationLog
from ..models.schemas import APIResponse
from ..utils.deps import get_db, get_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/pending", response_model=APIResponse)
async def get_pending_tasks(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    获取当前用户的待办任务
    OpenClaw 插件定时轮询此接口，根据 task_type 决定后续行为
    """
    try:
        # 查询用户需要处理的协商日志
        pending_logs = db.query(NegotiationLog).filter(
            NegotiationLog.user_id == current_user.id,
            NegotiationLog.action_required == True
        ).all()

        pending_tasks = []

        for log in pending_logs:
            meeting = db.query(Meeting).filter(Meeting.id == log.meeting_id).first()
            if not meeting:
                continue

            initiator = db.query(User).filter(User.id == meeting.initiator_id).first()

            # 根据是否有历史提交 + 会议状态确定任务类型和消息
            if meeting.status == "NEGOTIATING" and log.counter_proposal_message:
                # Coordinator 下发了妥协建议
                task_type = "COUNTER_PROPOSAL"
                message = log.counter_proposal_message
            elif not log.latest_slots or log.latest_slots == []:
                # 首次提交
                task_type = "INITIAL_SUBMIT"
                initiator_email = initiator.email if initiator else "未知"
                message = f"{initiator_email} 邀请您参加会议，请提供您的空闲时间。"
            else:
                # 需要重新提交但没有具体建议
                task_type = "COUNTER_PROPOSAL"
                message = f"协调助手提示：由于时间冲突，请重新提供您的空闲时间段。（第 {meeting.round_count} 轮协商）"

            pending_tasks.append({
                "meeting_id": meeting.id,
                "title": meeting.title,
                "initiator": initiator.email if initiator else "未知",
                "task_type": task_type,
                "message": message,
                "duration_minutes": meeting.duration_minutes,
                "round_count": meeting.round_count
            })

        return APIResponse(
            code=200,
            message="success",
            data={
                "pending_tasks": pending_tasks
            }
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
