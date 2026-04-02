from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from ..models.database import User, Meeting, NegotiationLog
from ..models.schemas import MeetingCreate, SubmitAvailabilityRequest, APIResponse, ResponseType
from ..utils.deps import get_db, get_current_user
from ..utils.token import generate_meeting_id, generate_token
from ..core.state_machine import StateMachine, MeetingState
import logging

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
state_logger = logging.getLogger("state")

# Global instance
state_machine = StateMachine(max_rounds=3)


@router.post("", response_model=APIResponse)
async def create_meeting(
        meeting_data: MeetingCreate,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    try:
        meeting_id = generate_meeting_id()

        # ---- 1. Create meeting with initial status PENDING ----
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

        # ---- 2. Write initiator negotiation log ----
        initiator_log = NegotiationLog(
            meeting_id=meeting_id,
            user_id=current_user.id,
            role="initiator",
            latest_slots=meeting_data.initiator_data.available_slots,
            preference_note=meeting_data.initiator_data.preference_note,
            action_required=False
        )
        db.add(initiator_log)

        # ---- 2.4 Check initiator email verification status ----
        if not current_user.email_verified:
            db.rollback()
            return APIResponse(
                code=403,
                message="Your email has not been verified yet. Please complete email verification via /api/auth/send-code and /api/auth/verify-bind first",
                data=None
            )

        # ---- 2.5 Check invitee email registration status ----
        unregistered = []
        for invitee_email in meeting_data.invitees:
            invitee = db.query(User).filter(User.email == invitee_email).first()
            if not invitee or not invitee.email_verified:
                unregistered.append(invitee_email)

        if unregistered:
            db.rollback()
            return APIResponse(
                code=400,
                message=f"The following invitees have not completed email registration. Please notify them to register first: {', '.join(unregistered)}",
                data={
                    "unregistered_emails": unregistered
                }
            )

        # ---- 3. Query user and create negotiation log for each invitee ----
        for invitee_email in meeting_data.invitees:
            invitee = db.query(User).filter(User.email == invitee_email).first()

            participant_log = NegotiationLog(
                meeting_id=meeting_id,
                user_id=invitee.id,
                role="participant",
                latest_slots=[],
                preference_note=None,
                action_required=True
            )
            db.add(participant_log)

        # ---- 4. State machine transition: PENDING -> COLLECTING (invitations sent) ----
        new_state = state_machine.transition(
            current=MeetingState.PENDING,
            target=MeetingState.COLLECTING,
            context={"meeting_id": meeting_id}
        )
        new_meeting.status = new_state.value
        state_logger.info(f"CREATED->COLLECTING | {meeting_id} | {meeting_data.title} | initiator={current_user.email} | invitees={meeting_data.invitees}")

        db.commit()

        return APIResponse(
            code=200,
            message="Meeting negotiation initiated, waiting for invitee responses",
            data={
                "id": meeting_id,              # Plugin expects "id"
                "meeting_id": meeting_id,       # Kept for backward compatibility
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
    """Get all meetings the current user is involved in (initiated + invited)"""
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
                "meeting_link": meeting.meeting_link,
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
            raise HTTPException(status_code=404, detail="Meeting not found")

        is_participant = db.query(NegotiationLog).filter(
            NegotiationLog.meeting_id == meeting_id,
            NegotiationLog.user_id == current_user.id
        ).first()

        if meeting.initiator_id != current_user.id and not is_participant:
            raise HTTPException(status_code=403, detail="No permission to view this meeting")

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
                "meeting_link": meeting.meeting_link,
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
            raise HTTPException(status_code=404, detail="Meeting not found")

        current_state = MeetingState(meeting.status)

        # COLLECTING allows time submission, FAILED allows initiator to cancel (REJECT) or re-initiate
        allowed_states = (MeetingState.COLLECTING, MeetingState.FAILED)
        if current_state not in allowed_states:
            raise HTTPException(
                status_code=400,
                detail=f"Current meeting status is {meeting.status}, submission not allowed"
            )
        # In FAILED status, only the initiator can take action
        if current_state == MeetingState.FAILED and current_user.id != meeting.initiator_id:
            raise HTTPException(
                status_code=403,
                detail="Meeting negotiation has failed. Only the initiator can cancel or re-initiate"
            )

        negotiation_log = db.query(NegotiationLog).filter(
            NegotiationLog.meeting_id == meeting_id,
            NegotiationLog.user_id == current_user.id
        ).first()

        if not negotiation_log:
            raise HTTPException(status_code=403, detail="You are not a participant of this meeting")

        # ---- Branch processing based on response_type ----

        if submit_data.response_type == ResponseType.REJECT:
            # ====== REJECT: Record rejection without interrupting collection, wait for all to complete before entering ANALYZING ======
            # In FAILED status, initiator REJECT = cancel meeting -> OVER
            if current_state == MeetingState.FAILED and current_user.id == meeting.initiator_id:
                # Initiator cancels meeting in FAILED phase -> OVER
                new_state = state_machine.transition(
                    current=MeetingState.FAILED,
                    target=MeetingState.OVER,
                    context={"meeting_id": meeting_id}
                )
                meeting.status = new_state.value
                meeting.updated_at = datetime.utcnow()
                state_logger.info(f"FAILED->OVER | {meeting_id} | {meeting.title} | initiator cancelled meeting")

                # Notify all invitees that the meeting has been cancelled
                all_logs = db.query(NegotiationLog).filter(
                    NegotiationLog.meeting_id == meeting_id
                ).all()
                for log in all_logs:
                    if log.user_id != current_user.id:
                        log.action_required = True
                        log.counter_proposal_message = (
                            f"Meeting cancelled\n"
                            f"Meeting: {meeting.title}\n"
                            f"The initiator has cancelled this meeting."
                        )
                        log.updated_at = datetime.utcnow()

                db.commit()
                return APIResponse(
                    code=200,
                    message="Meeting cancelled, all participants have been notified",
                    data={
                        "id": meeting_id, "meeting_id": meeting_id,
                        "response_type": "REJECT", "status": meeting.status,
                    }
                )

            # COLLECTING/NEGOTIATING phase: record rejection, do not interrupt collection
            reject_reason = submit_data.preference_note or "No rejection reason provided"

            # Mark rejector as done, record rejection reason and empty slots
            negotiation_log.action_required = False
            negotiation_log.latest_slots = []  # Empty slots = cannot attend
            negotiation_log.preference_note = f"[rejected] {reject_reason}"
            negotiation_log.counter_proposal_message = None
            negotiation_log.updated_at = datetime.utcnow()
            db.commit()

            state_logger.info(
                f"REJECT_RECORDED | {meeting_id} | {meeting.title} | "
                f"by={current_user.email} | reason={reject_reason} | continuing to collect others"
            )

            # Check if all have completed (same logic as INITIAL submission)
            all_logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            all_submitted = all(not p.action_required for p in all_logs)

            if all_submitted:
                # All completed -> ANALYZING, Agent will see this person's slots as empty + [rejected] tag
                _transition_to_analyzing(meeting, current_state, db)

            return APIResponse(
                code=200,
                message="Rejection recorded" + (", all participants have completed, awaiting coordination analysis" if all_submitted else ", waiting for other participants to submit"),
                data={
                    "id": meeting_id, "meeting_id": meeting_id,
                    "response_type": submit_data.response_type.value,
                    "status": meeting.status,
                    "all_submitted": all_submitted,
                }
            )

        elif submit_data.response_type == ResponseType.ACCEPT_PROPOSAL:
            # ====== ACCEPT_PROPOSAL: Accept compromise proposal ======
            # Write Agent's suggested time into latest_slots to ensure Agent uses correct data during ANALYZING
            if negotiation_log.suggested_slots:
                negotiation_log.latest_slots = negotiation_log.suggested_slots
            negotiation_log.action_required = False
            negotiation_log.counter_proposal_message = None
            negotiation_log.updated_at = datetime.utcnow()
            db.commit()

            # Check if everyone has accepted
            all_logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            all_accepted = all(not p.action_required for p in all_logs)

            if all_accepted:
                # All accepted -> transition to ANALYZING, waiting for Agent to poll and process
                _transition_to_analyzing(meeting, current_state, db)

            return APIResponse(
                code=200,
                message="Proposal accepted" + (", waiting for coordination Agent to analyze." if all_accepted else ", waiting for other participants to respond"),
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
            # ====== INITIAL / NEW_PROPOSAL: Submit time data ======

            # In FAILED status, initiator re-initiates -> FAILED -> COLLECTING
            if current_state == MeetingState.FAILED and current_user.id == meeting.initiator_id:
                new_state = state_machine.transition(
                    current=MeetingState.FAILED,
                    target=MeetingState.COLLECTING,
                    context={"meeting_id": meeting_id}
                )
                meeting.status = new_state.value
                meeting.round_count = 0
                meeting.updated_at = datetime.utcnow()

                # ---- Update meeting parameters (if initiator provided new ones) ----
                changes = []
                if submit_data.duration_minutes is not None and submit_data.duration_minutes != meeting.duration_minutes:
                    old_duration = meeting.duration_minutes
                    meeting.duration_minutes = submit_data.duration_minutes
                    changes.append(f"duration: {old_duration}->{submit_data.duration_minutes}min")

                state_logger.info(
                    f"FAILED->COLLECTING | {meeting_id} | {meeting.title} | initiator re-initiated | round_count reset to 0"
                    + (f" | param changes: {', '.join(changes)}" if changes else "")
                )

                # Update initiator's time
                negotiation_log.latest_slots = submit_data.available_slots
                negotiation_log.preference_note = submit_data.preference_note  # Overwrite unconditionally, clear old notes
                negotiation_log.action_required = False
                negotiation_log.counter_proposal_message = None
                negotiation_log.updated_at = datetime.utcnow()

                # ---- Handle participant changes ----
                if submit_data.invitees is not None:
                    new_invitee_set = set(submit_data.invitees)
                    # Cannot invite yourself
                    new_invitee_set.discard(current_user.email)

                    # Check if newly added invitees are registered
                    unregistered = []
                    for invitee_email in new_invitee_set:
                        invitee = db.query(User).filter(User.email == invitee_email).first()
                        if not invitee or not invitee.email_verified:
                            unregistered.append(invitee_email)
                    if unregistered:
                        db.rollback()
                        return APIResponse(
                            code=400,
                            message=f"The following invitees have not completed email registration: {', '.join(unregistered)}",
                            data={"unregistered_emails": unregistered}
                        )

                    # Current participants (excluding initiator)
                    existing_logs = db.query(NegotiationLog).filter(
                        NegotiationLog.meeting_id == meeting_id,
                        NegotiationLog.user_id != current_user.id
                    ).all()
                    existing_emails = {}
                    for log in existing_logs:
                        user = db.query(User).filter(User.id == log.user_id).first()
                        if user:
                            existing_emails[user.email] = log

                    # Remove participants no longer invited
                    for email, log in existing_emails.items():
                        if email not in new_invitee_set:
                            db.delete(log)
                            changes.append(f"removed: {email}")

                    # Add new participants
                    for invitee_email in new_invitee_set:
                        if invitee_email not in existing_emails:
                            invitee = db.query(User).filter(User.email == invitee_email).first()
                            new_log = NegotiationLog(
                                meeting_id=meeting_id,
                                user_id=invitee.id,
                                role="participant",
                                latest_slots=[],
                                preference_note=None,
                                action_required=True,
                                counter_proposal_message=(
                                    f"Meeting invitation\n"
                                    f"Meeting: {meeting.title}\n"
                                    f"The initiator invites you to this meeting. Please submit your available time."
                                ),
                            )
                            db.add(new_log)
                            changes.append(f"added: {invitee_email}")

                    if changes:
                        state_logger.info(f"participant changes | {meeting_id} | {', '.join(changes)}")

                # Build notification message
                change_desc = "The initiator has adjusted meeting parameters. " if changes else ""
                notify_msg = (
                    f"Meeting re-initiated\n"
                    f"Meeting: {meeting.title}\n"
                    f"{change_desc}Please resubmit your available time."
                )

                # Reset all invitees to pending submission (newly added ones are already set above)
                all_logs = db.query(NegotiationLog).filter(
                    NegotiationLog.meeting_id == meeting_id
                ).all()
                for log in all_logs:
                    if log.user_id != current_user.id:
                        log.action_required = True
                        log.latest_slots = []
                        log.preference_note = None  # Clear old notes to prevent residual false triggers
                        if not log.counter_proposal_message:  # Newly added participants already have a message
                            log.counter_proposal_message = notify_msg
                        log.suggested_slots = None
                        log.updated_at = datetime.utcnow()

                db.commit()
                return APIResponse(
                    code=200,
                    message="Meeting re-initiated, waiting for participants to resubmit time",
                    data={
                        "id": meeting_id, "meeting_id": meeting_id,
                        "response_type": submit_data.response_type.value,
                        "status": meeting.status,
                        "all_submitted": False,
                        "changes": changes if changes else None,
                    }
                )

            # Normal COLLECTING phase submission
            negotiation_log.latest_slots = submit_data.available_slots
            if submit_data.preference_note:
                negotiation_log.preference_note = submit_data.preference_note
            negotiation_log.action_required = False
            negotiation_log.counter_proposal_message = None
            negotiation_log.updated_at = datetime.utcnow()
            db.commit()

            # Check if all participants have submitted
            all_logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            all_submitted = all(not p.action_required for p in all_logs)

            if all_submitted:
                # All submitted -> transition to ANALYZING, waiting for Agent to poll and process
                _transition_to_analyzing(meeting, current_state, db)

            return APIResponse(
                code=200,
                message="Submission successful" + (", server-side coordination Agent recalculation triggered." if all_submitted else ""),
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
    After all submit / all accept, transition meeting status to ANALYZING.
    Subsequently handled by the external Coordinator Agent polling /api/agent/tasks/pending.
    """
    new_state = state_machine.transition(
        current=current_state,
        target=MeetingState.ANALYZING,
        context={"meeting_id": meeting.id}
    )
    meeting.status = new_state.value
    meeting.updated_at = datetime.utcnow()
    state_logger.info(f"{current_state.value}->ANALYZING | {meeting.id} | {meeting.title} | all submitted")
    db.commit()
