from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime
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
    Get pending tasks for the current user.
    The OpenClaw plugin polls this endpoint periodically and decides subsequent behavior based on task_type.
    """
    try:
        # Query negotiation logs that require user action
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

            # Determine task type and message based on meeting status + user role
            if meeting.status == "CONFIRMED" and log.counter_proposal_message:
                # CONFIRMED: All receive confirmation notification
                task_type = "MEETING_CONFIRMED"
                message = log.counter_proposal_message
            elif meeting.status == "FAILED" and log.counter_proposal_message:
                # FAILED: Only initiator receives (invitees have action_required=False, won't enter here)
                task_type = "MEETING_FAILED"
                message = log.counter_proposal_message
            elif meeting.status == "OVER" and log.counter_proposal_message:
                # OVER: Invitees receive cancellation notification
                task_type = "MEETING_OVER"
                message = log.counter_proposal_message
            elif log.counter_proposal_message:
                # COLLECTING or NEGOTIATING status with Agent's compromise suggestion (multi-round negotiation)
                task_type = "COUNTER_PROPOSAL"
                message = log.counter_proposal_message
            elif not log.latest_slots or log.latest_slots == []:
                # Initial submit -- include initiator's available time slots for invitee reference
                task_type = "INITIAL_SUBMIT"
                initiator_email = initiator.email if initiator else "unknown"

                # Query initiator's time slots
                initiator_log = db.query(NegotiationLog).filter(
                    NegotiationLog.meeting_id == meeting.id,
                    NegotiationLog.role == "initiator"
                ).first()
                initiator_slots = initiator_log.latest_slots if initiator_log and initiator_log.latest_slots else []

                if initiator_slots:
                    slots_text = ", ".join(initiator_slots[:5])
                    message = f"{initiator_email} invites you to the meeting \"{meeting.title}\" ({meeting.duration_minutes} minutes).\nInitiator's available time: {slots_text}\nPlease provide your available time."
                else:
                    message = f"{initiator_email} invites you to a meeting. Please provide your available time."
            else:
                # Need to resubmit but no specific suggestion
                task_type = "COUNTER_PROPOSAL"
                message = f"Coordination assistant notice: Due to time conflicts, please resubmit your available time slots. (Round {meeting.round_count} negotiation)"

            # Get initiator time slots (for all task types)
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
                "initiator": initiator.email if initiator else "unknown",
                "task_type": task_type,
                "message": message,
                "suggested_slots": log.suggested_slots or [],
                "initiator_slots": _initiator_slots,
                "duration_minutes": meeting.duration_minutes,
                "round_count": meeting.round_count,
                "meeting_link": meeting.meeting_link
            })

            # Notification-type tasks (CONFIRMED/FAILED/OVER): consume on read, prevent duplicate pushes
            if task_type in ("MEETING_CONFIRMED", "MEETING_FAILED", "MEETING_OVER"):
                log.action_required = False
                log.updated_at = datetime.utcnow()

        # Batch commit notification consumption state changes
        if pending_tasks:
            db.commit()

        return APIResponse(
            code=200,
            message="success",
            data={
                "pending_tasks": pending_tasks
            }
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
