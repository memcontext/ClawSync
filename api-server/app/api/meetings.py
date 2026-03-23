from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from ..models.database import User, Meeting, NegotiationLog
from ..models.schemas import MeetingCreate, SubmitAvailabilityRequest, APIResponse, ResponseType
from ..utils.deps import get_db, get_current_user
from ..utils.token import generate_meeting_id
from ..core.state_machine import StateMachine, MeetingState
import logging

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
state_logger = logging.getLogger("state")

# 全局实例
state_machine = StateMachine(max_rounds=3)


@router.post("", response_model=APIResponse)
async def create_meeting(
        meeting_data: MeetingCreate,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    try:
        meeting_id = generate_meeting_id()

        # ---- 1. 创建会议，初始状态 PENDING ----
        new_meeting = Meeting(
            id=meeting_id,
            initiator_id=current_user.id,
            title=meeting_data.title,
            duration_minutes=meeting_data.duration_minutes,
            status=MeetingState.PENDING.value,
            round_count=0,
            created_at=datetime.utcnow()
        )
        db.add(new_meeting)

        # ---- 2. 写入发起人协商日志 ----
        initiator_log = NegotiationLog(
            meeting_id=meeting_id,
            user_id=current_user.id,
            role="initiator",
            latest_slots=meeting_data.initiator_data.available_slots,
            preference_note=meeting_data.initiator_data.preference_note,
            action_required=False
        )
        db.add(initiator_log)

        # ---- 3. 为每位受邀人创建用户（如不存在）及协商日志 ----
        from ..utils.token import generate_token
        for invitee_email in meeting_data.invitees:
            invitee = db.query(User).filter(User.email == invitee_email).first()
            if not invitee:
                invitee = User(
                    email=invitee_email,
                    token=generate_token(invitee_email),
                    created_at=datetime.utcnow()
                )
                db.add(invitee)
                db.flush()

            participant_log = NegotiationLog(
                meeting_id=meeting_id,
                user_id=invitee.id,
                role="participant",
                latest_slots=[],
                preference_note=None,
                action_required=True
            )
            db.add(participant_log)

        # ---- 4. 状态机流转：PENDING → COLLECTING（发出邀请） ----
        new_state = state_machine.transition(
            current=MeetingState.PENDING,
            target=MeetingState.COLLECTING,
            context={"meeting_id": meeting_id}
        )
        new_meeting.status = new_state.value
        state_logger.info(f"CREATED→COLLECTING | {meeting_id} | {meeting_data.title} | initiator={current_user.email} | invitees={meeting_data.invitees}")

        db.commit()

        return APIResponse(
            code=200,
            message="会议协商已发起，等待受邀人响应",
            data={
                "id": meeting_id,              # 插件期望 "id"
                "meeting_id": meeting_id,       # 保持向后兼容
                "title": meeting_data.title,
                "status": new_meeting.status,
                "duration_minutes": meeting_data.duration_minutes,
                "invitees": meeting_data.invitees,
                "initiator_data": meeting_data.initiator_data.model_dump()
            }
        )

    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=APIResponse)
async def list_my_meetings(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """获取当前用户参与的所有会议（发起的 + 受邀的）"""
    try:
        my_logs = db.query(NegotiationLog).filter(
            NegotiationLog.user_id == current_user.id
        ).all()

        meeting_ids = list({log.meeting_id for log in my_logs})

        meetings_list = []
        for mid in meeting_ids:
            meeting = db.query(Meeting).filter(Meeting.id == mid).first()
            if not meeting:
                continue

            user_log = next((l for l in my_logs if l.meeting_id == mid), None)
            initiator = db.query(User).filter(User.id == meeting.initiator_id).first()

            all_logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == mid
            ).all()
            total = len(all_logs)
            submitted = sum(1 for l in all_logs if not l.action_required)

            meetings_list.append({
                "meeting_id": meeting.id,
                "title": meeting.title,
                "status": meeting.status,
                "my_role": user_log.role if user_log else "unknown",
                "action_required": user_log.action_required if user_log else False,
                "initiator_email": initiator.email if initiator else "unknown",
                "duration_minutes": meeting.duration_minutes,
                "round_count": meeting.round_count,
                "final_time": meeting.final_time,
                "progress": f"{submitted}/{total}",
                "created_at": meeting.created_at.isoformat() if meeting.created_at else None
            })

        meetings_list.sort(key=lambda x: x["created_at"] or "", reverse=True)

        return APIResponse(
            code=200,
            message="success",
            data={
                "total": len(meetings_list),
                "meetings": meetings_list
            }
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{meeting_id}", response_model=APIResponse)
async def get_meeting_status(
        meeting_id: str,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()

        if not meeting:
            raise HTTPException(status_code=404, detail="会议不存在")

        is_participant = db.query(NegotiationLog).filter(
            NegotiationLog.meeting_id == meeting_id,
            NegotiationLog.user_id == current_user.id
        ).first()

        if meeting.initiator_id != current_user.id and not is_participant:
            raise HTTPException(status_code=403, detail="无权查看此会议")

        logs = db.query(NegotiationLog).filter(
            NegotiationLog.meeting_id == meeting_id
        ).all()

        participants_info = []
        for log in logs:
            user = db.query(User).filter(User.id == log.user_id).first()
            participants_info.append({
                "email": user.email if user else "unknown",
                "role": log.role,
                "has_submitted": not log.action_required,
                "latest_slots": log.latest_slots,
                "preference_note": log.preference_note
            })

        return APIResponse(
            code=200,
            message="success",
            data={
                "meeting_id": meeting.id,
                "title": meeting.title,
                "status": meeting.status,
                "round_count": meeting.round_count,
                "final_time": meeting.final_time,
                "coordinator_reasoning": meeting.coordinator_reasoning,
                "participants": participants_info
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{meeting_id}/submit", response_model=APIResponse)
async def submit_availability(
        meeting_id: str,
        submit_data: SubmitAvailabilityRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not meeting:
            raise HTTPException(status_code=404, detail="会议不存在")

        current_state = MeetingState(meeting.status)

        # 只有 COLLECTING 或 NEGOTIATING 状态才允许提交
        if current_state not in (MeetingState.COLLECTING, MeetingState.NEGOTIATING):
            raise HTTPException(
                status_code=400,
                detail=f"当前会议状态为 {meeting.status}，不允许提交"
            )

        negotiation_log = db.query(NegotiationLog).filter(
            NegotiationLog.meeting_id == meeting_id,
            NegotiationLog.user_id == current_user.id
        ).first()

        if not negotiation_log:
            raise HTTPException(status_code=403, detail="您不是此会议的参与者")

        # ---- 根据 response_type 分支处理 ----

        if submit_data.response_type == ResponseType.REJECT:
            # ====== REJECT：拒绝会议邀请或协商方案，会议直接失败 ======
            # COLLECTING 阶段：拒绝会议邀请
            # NEGOTIATING 阶段：拒绝协商方案
            is_collecting = current_state == MeetingState.COLLECTING
            reject_reason = (
                f"用户 {current_user.email} 拒绝了会议邀请"
                if is_collecting
                else f"用户 {current_user.email} 拒绝了协商方案"
            )

            negotiation_log.action_required = False
            negotiation_log.preference_note = submit_data.preference_note or reject_reason
            negotiation_log.counter_proposal_message = None
            negotiation_log.updated_at = datetime.utcnow()

            fail_state = state_machine.transition(
                current=current_state,
                target=MeetingState.FAILED,
                context={
                    "meeting_id": meeting_id,
                    "reason": reject_reason
                }
            )
            meeting.status = fail_state.value
            meeting.coordinator_reasoning = f"协商失败：{reject_reason}"
            meeting.updated_at = datetime.utcnow()
            state_logger.info(f"REJECTED→FAILED | {meeting_id} | {meeting.title} | by={current_user.email} | reason={reject_reason}")

            # 通知所有其他参与者会议已失败
            all_logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            for log in all_logs:
                if log.user_id != current_user.id:
                    log.action_required = True
                    log.counter_proposal_message = (
                        f"❌ 会议协商失败\n"
                        f"会议：{meeting.title}\n"
                        f"原因：{reject_reason}\n"
                        f"如需重新发起，请创建新的会议。"
                    )
                    log.updated_at = datetime.utcnow()

            db.commit()

            return APIResponse(
                code=200,
                message="已拒绝，会议协商终止",
                data={
                    "id": meeting_id,
                    "meeting_id": meeting_id,
                    "response_type": submit_data.response_type.value,
                    "status": meeting.status,
                    "all_submitted": False,
                    "coordinator_result": None,
                    "created_at": negotiation_log.created_at.isoformat() if negotiation_log.created_at else None,
                    "updated_at": negotiation_log.updated_at.isoformat() if negotiation_log.updated_at else None
                }
            )

        elif submit_data.response_type == ResponseType.ACCEPT_PROPOSAL:
            # ====== ACCEPT_PROPOSAL：接受妥协方案 ======
            negotiation_log.action_required = False
            negotiation_log.counter_proposal_message = None
            negotiation_log.updated_at = datetime.utcnow()
            db.commit()

            # 检查是否所有人都已接受
            all_logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            all_accepted = all(not p.action_required for p in all_logs)

            if all_accepted:
                # 全员接受 → 转入 ANALYZING，等待 Agent 轮询处理
                _transition_to_analyzing(meeting, current_state, db)

            return APIResponse(
                code=200,
                message="已接受方案" + ("，等待协调 Agent 分析。" if all_accepted else "，等待其他参与者响应"),
                data={
                    "id": meeting_id,
                    "meeting_id": meeting_id,
                    "response_type": submit_data.response_type.value,
                    "status": meeting.status,
                    "all_submitted": all_accepted,
                    "coordinator_result": None,
                    "created_at": negotiation_log.created_at.isoformat() if negotiation_log.created_at else None,
                    "updated_at": negotiation_log.updated_at.isoformat() if negotiation_log.updated_at else None
                }
            )

        else:
            # ====== INITIAL / NEW_PROPOSAL：提交时间数据 ======
            negotiation_log.latest_slots = submit_data.available_slots
            if submit_data.preference_note:
                negotiation_log.preference_note = submit_data.preference_note
            negotiation_log.action_required = False
            negotiation_log.counter_proposal_message = None
            negotiation_log.updated_at = datetime.utcnow()
            db.commit()

            # 检查是否所有参与者都已提交
            all_logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            all_submitted = all(not p.action_required for p in all_logs)

            if all_submitted:
                # 全员提交 → 转入 ANALYZING，等待 Agent 轮询处理
                _transition_to_analyzing(meeting, current_state, db)

            return APIResponse(
                code=200,
                message="提交成功" + ("，已触发服务端协调 Agent 重新计算。" if all_submitted else ""),
                data={
                    "id": meeting_id,
                    "meeting_id": meeting_id,
                    "response_type": submit_data.response_type.value,
                    "status": meeting.status,
                    "all_submitted": all_submitted,
                    "coordinator_result": None,
                    "created_at": negotiation_log.created_at.isoformat() if negotiation_log.created_at else None,
                    "updated_at": negotiation_log.updated_at.isoformat() if negotiation_log.updated_at else None
                }
            )

    except HTTPException:
        raise
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


def _transition_to_analyzing(meeting: Meeting, current_state: MeetingState, db: Session):
    """
    全员提交/全员接受后，将会议状态转入 ANALYZING。
    后续由外部 Coordinator Agent 通过 /api/agent/tasks/pending 轮询并处理。
    """
    new_state = state_machine.transition(
        current=current_state,
        target=MeetingState.ANALYZING,
        context={"meeting_id": meeting.id}
    )
    meeting.status = new_state.value
    meeting.updated_at = datetime.utcnow()
    state_logger.info(f"{current_state.value}→ANALYZING | {meeting.id} | {meeting.title} | 全员已提交")
    db.commit()
