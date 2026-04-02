"""
Coordinator Agent API

API 7: GET  /api/agent/tasks/pending           -- Agent polls for meetings pending coordination
API 8: POST /api/agent/meetings/{id}/result     -- Agent submits coordination decision result

These two endpoints are called by the external Coordinator Agent, decoupling LLM inference from the API Server.
Flow:
  1. All participants submit -> meeting status becomes ANALYZING
  2. Agent polls API 7 -> retrieves meetings in ANALYZING status with participant data
  3. Agent runs LLM inference locally
  4. Agent calls API 8 -> writes decision result back to database, driving state transitions
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime
import logging

from ..models.database import User, Meeting, NegotiationLog
from ..models.schemas import APIResponse, AgentCoordinationResult, DecisionStatus
from ..utils.deps import get_db
from ..core.state_machine import StateMachine, MeetingState

router = APIRouter(prefix="/api/agent", tags=["agent"])
state_logger = logging.getLogger("state")

state_machine = StateMachine(max_rounds=3)


@router.get("/tasks/pending", response_model=APIResponse)
async def get_agent_pending_tasks(db: Session = Depends(get_db)):
    """
    API 7: Get pending coordination tasks

    Returns all meetings in ANALYZING status, including each participant's
    reported time slots and preference data, for the Coordinator Agent to perform LLM inference.
    """
    try:
        # Query all meetings in ANALYZING status
        analyzing_meetings = db.query(Meeting).filter(
            Meeting.status == MeetingState.ANALYZING.value
        ).all()

        pending_tasks = []

        for meeting in analyzing_meetings:
            # Get all negotiation logs for this meeting
            logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting.id
            ).all()

            participants_data = []
            for log in logs:
                user = db.query(User).filter(User.id == log.user_id).first()

                # Convert slots to {start, end} dict format (format expected by Agent)
                formatted_slots = _format_slots_for_agent(log.latest_slots or [])

                participants_data.append({
                    "user_id": log.user_id,
                    "email": user.email if user else "unknown",
                    "role": log.role,
                    "latest_slots": formatted_slots,
                    "preference_note": log.preference_note
                })

            pending_tasks.append({
                "meeting_id": meeting.id,
                "title": meeting.title,
                "duration_minutes": meeting.duration_minutes,
                "round_count": meeting.round_count,
                "max_rounds": state_machine.max_rounds,
                "previous_reasoning": meeting.coordinator_reasoning,
                "participants_data": participants_data
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


@router.post("/meetings/{meeting_id}/result", response_model=APIResponse)
async def submit_coordination_result(
        meeting_id: str,
        result: AgentCoordinationResult,
        db: Session = Depends(get_db)
):
    """
    API 8: Submit coordination decision result

    After the Coordinator Agent completes LLM inference, it calls this endpoint to write the decision back to the database.
    Drives state machine transitions based on decision_status:
      - CONFIRMED  -> set final_time, meeting completed
      - NEGOTIATING -> increment round, write counter_proposals to participant logs
      - FAILED     -> meeting terminated
    """
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")

        current_state = MeetingState(meeting.status)

        # Only accept meetings in ANALYZING status
        if current_state != MeetingState.ANALYZING:
            raise HTTPException(
                status_code=400,
                detail=f"Meeting is currently in {meeting.status} status; only ANALYZING status allows submitting coordination results"
            )

        # ---- Drive state transitions based on Agent decision ----

        if result.decision_status == DecisionStatus.CONFIRMED:
            # ====== Scenario A: Time match successful -> CONFIRMED ======
            new_state = state_machine.transition(
                current=MeetingState.ANALYZING,
                target=MeetingState.CONFIRMED,
                context={
                    "meeting_id": meeting_id,
                    "final_time": result.final_time
                }
            )
            meeting.status = new_state.value
            meeting.final_time = result.final_time
            meeting.coordinator_reasoning = result.agent_reasoning
            meeting.updated_at = datetime.utcnow()
            state_logger.info(f"CONFIRMED | {meeting_id} | {meeting.title} | final_time={result.final_time}")

            # CONFIRMED: Auto-generate Zoom meeting link
            if not meeting.meeting_link:
                from ..services.zoom_meeting_service import create_zoom_meeting
                zoom_result = create_zoom_meeting(
                    title=meeting.title,
                    duration_minutes=meeting.duration_minutes,
                )
                if zoom_result:
                    meeting.meeting_link = zoom_result["join_url"]

            # CONFIRMED: All participants receive confirmation notification
            logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            link_text = f"\nMeeting link: {meeting.meeting_link}" if meeting.meeting_link else ""

            # Check if all participants were auto-processed by AI (no human intervention)
            all_auto = all(
                (log.preference_note or "").startswith("[auto]")
                for log in logs
            )

            for log in logs:
                log.action_required = True
                auto_note = ""
                if (log.preference_note or "").startswith("[auto]"):
                    auto_note = "\n Your time was automatically submitted by the AI assistant based on your calendar and memory. You were not disturbed."
                if all_auto:
                    auto_note += "\n This meeting was fully auto-negotiated by the AI assistant. No participants were disturbed."
                log.counter_proposal_message = (
                    f"Meeting confirmed!\n"
                    f"Meeting: {meeting.title}\n"
                    f"Time: {result.final_time}\n"
                    f"Duration: {meeting.duration_minutes} minutes{link_text}"
                    f"{auto_note}\n"
                    f"Please confirm your attendance."
                )
                log.updated_at = datetime.utcnow()

            # Send meeting confirmation email to all participants
            from ..services.email_service import send_meeting_confirmed_email
            initiator = db.query(User).filter(User.id == meeting.initiator_id).first()
            initiator_email = initiator.email if initiator else "unknown"
            for log in logs:
                user = db.query(User).filter(User.id == log.user_id).first()
                if user and user.email:
                    send_meeting_confirmed_email(
                        to_email=user.email,
                        meeting_title=meeting.title,
                        final_time=result.final_time,
                        duration_minutes=meeting.duration_minutes,
                        meeting_link=meeting.meeting_link,
                        initiator_email=initiator_email,
                    )

        elif result.decision_status == DecisionStatus.NEGOTIATING:
            # ====== Scenario B: Negotiation needed -> ANALYZING -> COLLECTING (re-collect) ======
            meeting.round_count += 1

            try:
                # ANALYZING -> COLLECTING (validate round count limit)
                new_state = state_machine.transition(
                    current=MeetingState.ANALYZING,
                    target=MeetingState.COLLECTING,
                    context={
                        "meeting_id": meeting_id,
                        "round_count": meeting.round_count
                    }
                )
                meeting.status = new_state.value  # COLLECTING
                meeting.coordinator_reasoning = result.agent_reasoning
                meeting.updated_at = datetime.utcnow()
                target_emails_list = [p.target_email for p in result.counter_proposals]
                state_logger.info(f"ANALYZING->COLLECTING | {meeting_id} | {meeting.title} | round={meeting.round_count} | targets={target_emails_list}")

                # Build set of user emails that need to resubmit
                target_emails = {p.target_email for p in result.counter_proposals}

                # Get all negotiation logs and build email -> log mapping
                logs = db.query(NegotiationLog).filter(
                    NegotiationLog.meeting_id == meeting_id
                ).all()

                email_to_log = {}
                for log in logs:
                    user = db.query(User).filter(User.id == log.user_id).first()
                    if user:
                        email_to_log[user.email] = log

                # Only mark users in counter_proposals as action required
                for email, log in email_to_log.items():
                    if email in target_emails:
                        log.action_required = True
                        proposal = next(
                            (p for p in result.counter_proposals if p.target_email == email),
                            None
                        )
                        if proposal:
                            log.counter_proposal_message = proposal.message
                            log.suggested_slots = proposal.suggested_slots
                        else:
                            log.counter_proposal_message = None
                            log.suggested_slots = None
                    else:
                        log.action_required = False
                        log.counter_proposal_message = None
                        log.suggested_slots = None
                    log.updated_at = datetime.utcnow()

                # Prevent state deadlock: if everyone is done (no one needs action), go directly to ANALYZING
                all_done = all(not log.action_required for log in logs)
                if all_done:
                    reanalyze_state = state_machine.transition(
                        current=MeetingState.COLLECTING,
                        target=MeetingState.ANALYZING,
                        context={"meeting_id": meeting_id}
                    )
                    meeting.status = reanalyze_state.value
                    meeting.updated_at = datetime.utcnow()
                    state_logger.info(
                        f"COLLECTING->ANALYZING(auto) | {meeting_id} | {meeting.title} | "
                        f"all done, auto re-entering analysis"
                    )

            except ValueError:
                # Exceeded max rounds -> FAILED
                logs = db.query(NegotiationLog).filter(
                    NegotiationLog.meeting_id == meeting_id
                ).all()

                fail_state = state_machine.transition(
                    current=MeetingState.ANALYZING,
                    target=MeetingState.FAILED,
                    context={
                        "meeting_id": meeting_id,
                        "reason": "Exceeded maximum negotiation rounds"
                    }
                )
                meeting.status = fail_state.value
                meeting.coordinator_reasoning = "Negotiation failed: maximum negotiation round limit reached"
                meeting.updated_at = datetime.utcnow()
                state_logger.info(f"FAILED(MAX_ROUNDS) | {meeting_id} | {meeting.title} | round={meeting.round_count}")

                # FAILED: Notify initiator only, with participant details
                _notify_failed_initiator_only(meeting, logs, "Maximum negotiation round limit reached", db)

        elif result.decision_status == DecisionStatus.FAILED:
            # ====== Scenario C: Complete failure -> FAILED ======
            new_state = state_machine.transition(
                current=MeetingState.ANALYZING,
                target=MeetingState.FAILED,
                context={
                    "meeting_id": meeting_id,
                    "reason": result.agent_reasoning
                }
            )
            meeting.status = new_state.value
            meeting.coordinator_reasoning = result.agent_reasoning
            meeting.updated_at = datetime.utcnow()

            # FAILED: Notify initiator only, with participant details
            logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            _notify_failed_initiator_only(meeting, logs, result.agent_reasoning, db)

        db.commit()

        return APIResponse(
            code=200,
            message="Coordination result applied successfully, system status updated",
            data={
                "meeting_id": meeting_id,
                "new_status": meeting.status
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


def _notify_failed_initiator_only(meeting, logs, reason, db):
    """
    On FAILED, notify only the initiator with detailed time information and rejection reasons for all participants.
    Invitees are not notified yet (waiting for initiator's decision: cancel -> OVER or re-initiate -> COLLECTING).
    """
    # Build participant details
    participants_info = []
    for log in logs:
        user = db.query(User).filter(User.id == log.user_id).first()
        email = user.email if user else "unknown"
        if log.preference_note and log.preference_note.startswith("[rejected]"):
            status = f"Rejected ({log.preference_note.replace('[rejected] ', '')})"
        elif not log.latest_slots:
            status = "Not submitted"
        else:
            slots_preview = ", ".join(log.latest_slots[:3])
            status = f"Available: {slots_preview}"
        # Append preference_note (notes from non-rejectors, e.g. "suggest changing to 30 minutes")
        note = (log.preference_note or "").strip()
        if note and not note.startswith("[rejected]"):
            status += f" | Note: {note}"
        participants_info.append(f"  {email}: {status}")
    detail = "\n".join(participants_info)

    for log in logs:
        if log.user_id == meeting.initiator_id:
            log.action_required = True
            log.counter_proposal_message = (
                f"Negotiation failed\n"
                f"Meeting: {meeting.title}\n"
                f"Reason: {reason}\n"
                f"Participant status:\n{detail}\n\n"
                f"You can choose to:\n"
                f"  - Cancel the meeting (reject)\n"
                f"  - Adjust the time and re-initiate"
            )
        else:
            # Invitees not notified yet, waiting for initiator's decision
            log.action_required = False
            log.counter_proposal_message = None
        log.suggested_slots = None
        log.updated_at = datetime.utcnow()


def _format_slots_for_agent(slots: list) -> list:
    """
    Convert time slot format from database to {start, end} dict format expected by Agent

    Input may be:
      - String "2026-03-18 14:00-18:00" -> {"start": "2026-03-18 14:00", "end": "2026-03-18 18:00"}
      - Dict {"start": "...", "end": "..."} -> returned as-is
    """
    formatted = []
    for slot in slots:
        if isinstance(slot, dict) and "start" in slot and "end" in slot:
            formatted.append(slot)
        elif isinstance(slot, str) and "-" in slot:
            # Try to parse "2026-03-18 14:00-18:00" format
            # Split by the last "-" before the date part
            parts = slot.rsplit("-", 1)
            if len(parts) == 2:
                start_part = parts[0].strip()
                end_time = parts[1].strip()

                # Extract date part (if end is time-only, prepend date)
                date_part = ""
                if " " in start_part:
                    date_part = start_part.split(" ")[0]

                if len(end_time) <= 5 and date_part:
                    # end is time-only like "18:00", need to prepend date
                    end_full = f"{date_part} {end_time}"
                else:
                    end_full = end_time

                formatted.append({
                    "start": start_part,
                    "end": end_full
                })
            else:
                formatted.append({"start": slot, "end": slot})
        else:
            formatted.append({"start": str(slot), "end": str(slot)})

    return formatted
