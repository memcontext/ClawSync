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

            # 根据会议状态 + 日志内容确定任务类型和消息
            if meeting.status == "CONFIRMED" and log.counter_proposal_message:
                # 会议已确认 → 通知用户
                task_type = "MEETING_CONFIRMED"
                message = log.counter_proposal_message
            elif meeting.status == "FAILED" and log.counter_proposal_message:
                # 会议协商失败 → 通知用户
                task_type = "MEETING_FAILED"
                message = log.counter_proposal_message
            elif log.counter_proposal_message:
                # COLLECTING 或 NEGOTIATING 状态下，有 Agent 的妥协建议（多轮协商）
                task_type = "COUNTER_PROPOSAL"
                message = log.counter_proposal_message
            elif not log.latest_slots or log.latest_slots == []:
                # 首次提交 — 附带发起人的可用时间段，帮助被邀请人参考
                task_type = "INITIAL_SUBMIT"
                initiator_email = initiator.email if initiator else "未知"

                # 查询发起人的时间槽
                initiator_log = db.query(NegotiationLog).filter(
                    NegotiationLog.meeting_id == meeting.id,
                    NegotiationLog.role == "initiator"
                ).first()
                initiator_slots = initiator_log.latest_slots if initiator_log and initiator_log.latest_slots else []

                if initiator_slots:
                    slots_text = "、".join(initiator_slots[:5])
                    message = f"{initiator_email} 邀请您参加会议「{meeting.title}」（{meeting.duration_minutes}分钟）。\n发起人可用时间：{slots_text}\n请提供您的空闲时间。"
                else:
                    message = f"{initiator_email} 邀请您参加会议，请提供您的空闲时间。"
            else:
                # 需要重新提交但没有具体建议
                task_type = "COUNTER_PROPOSAL"
                message = f"协调助手提示：由于时间冲突，请重新提供您的空闲时间段。（第 {meeting.round_count} 轮协商）"

            # 获取发起人时间槽（用于所有任务类型）
            if task_type == "INITIAL_SUBMIT":
                _initiator_log = db.query(NegotiationLog).filter(
                    NegotiationLog.meeting_id == meeting.id,
                    NegotiationLog.role == "initiator"
                ).first()
                _initiator_slots = _initiator_log.latest_slots if _initiator_log and _initiator_log.latest_slots else []
            else:
                _initiator_slots = []

            pending_tasks.append({
                "meeting_id": meeting.id,
                "title": meeting.title,
                "initiator": initiator.email if initiator else "未知",
                "task_type": task_type,
                "message": message,
                "suggested_slots": log.suggested_slots or [],
                "initiator_slots": _initiator_slots,
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
