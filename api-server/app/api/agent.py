"""
Coordinator Agent 专用接口

API 7: GET  /api/agent/tasks/pending           — Agent 轮询待协调的会议
API 8: POST /api/agent/meetings/{id}/result     — Agent 提交协调决策结果

这两个接口供外部 Coordinator Agent 调用，将 LLM 推理与 API Server 解耦。
流程:
  1. 全员提交 → 会议状态变为 ANALYZING
  2. Agent 轮询 API 7 → 获取 ANALYZING 状态的会议及参与者数据
  3. Agent 本地运行 LLM 推理
  4. Agent 调用 API 8 → 将决策结果写回数据库，驱动状态流转
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
    API 7: 获取待协调的会议任务

    返回所有状态为 ANALYZING 的会议，包含每个参与者上报的时间与偏好数据，
    供 Coordinator Agent 进行 LLM 推理。
    """
    try:
        # 查询所有 ANALYZING 状态的会议
        analyzing_meetings = db.query(Meeting).filter(
            Meeting.status == MeetingState.ANALYZING.value
        ).all()

        pending_tasks = []

        for meeting in analyzing_meetings:
            # 获取该会议的所有协商日志
            logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting.id
            ).all()

            participants_data = []
            for log in logs:
                user = db.query(User).filter(User.id == log.user_id).first()

                # 将 slots 转换为 {start, end} 字典格式（Agent 期望的格式）
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
    API 8: 提交协调决策结果

    Coordinator Agent 完成 LLM 推理后，调用此接口将决策写回数据库。
    根据 decision_status 驱动状态机流转：
      - CONFIRMED  → 设置 final_time，会议完成
      - NEGOTIATING → 增加轮次，将 counter_proposals 写入各参与者日志
      - FAILED     → 会议终止
    """
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not meeting:
            raise HTTPException(status_code=404, detail="会议不存在")

        current_state = MeetingState(meeting.status)

        # 只接受 ANALYZING 状态的会议
        if current_state != MeetingState.ANALYZING:
            raise HTTPException(
                status_code=400,
                detail=f"会议当前状态为 {meeting.status}，只有 ANALYZING 状态才能提交协调结果"
            )

        # ---- 根据 Agent 决策驱动状态流转 ----

        if result.decision_status == DecisionStatus.CONFIRMED:
            # ====== 场景 A: 时间匹配成功 → CONFIRMED ======
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

            # CONFIRMED：全体参会人统一收到确认通知
            logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            for log in logs:
                log.action_required = True
                log.counter_proposal_message = (
                    f"✅ 会议已确认！\n"
                    f"会议：{meeting.title}\n"
                    f"时间：{result.final_time}\n"
                    f"时长：{meeting.duration_minutes} 分钟\n"
                    f"请确认参加。"
                )
                log.updated_at = datetime.utcnow()

        elif result.decision_status == DecisionStatus.NEGOTIATING:
            # ====== 场景 B: 需要协商 → ANALYZING → COLLECTING（重新收集） ======
            meeting.round_count += 1

            try:
                # ANALYZING → COLLECTING（验证轮次是否超限）
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
                state_logger.info(f"ANALYZING→COLLECTING | {meeting_id} | {meeting.title} | round={meeting.round_count} | targets={target_emails_list}")

                # 构建需要重新提交的用户邮箱集合
                target_emails = {p.target_email for p in result.counter_proposals}

                # 获取所有协商日志并构建 email → log 映射
                logs = db.query(NegotiationLog).filter(
                    NegotiationLog.meeting_id == meeting_id
                ).all()

                email_to_log = {}
                for log in logs:
                    user = db.query(User).filter(User.id == log.user_id).first()
                    if user:
                        email_to_log[user.email] = log

                # 只标记 counter_proposals 中的用户为需要操作
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

                # 防止状态卡死：如果所有人都已完成（无人需要操作），直接转入 ANALYZING
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
                        f"COLLECTING→ANALYZING(auto) | {meeting_id} | {meeting.title} | "
                        f"所有人已完成，自动重新进入分析"
                    )

            except ValueError:
                # 超过最大轮数 → FAILED
                logs = db.query(NegotiationLog).filter(
                    NegotiationLog.meeting_id == meeting_id
                ).all()

                fail_state = state_machine.transition(
                    current=MeetingState.ANALYZING,
                    target=MeetingState.FAILED,
                    context={
                        "meeting_id": meeting_id,
                        "reason": "超过最大协商轮数"
                    }
                )
                meeting.status = fail_state.value
                meeting.coordinator_reasoning = "协商失败：已达最大协商轮数限制"
                meeting.updated_at = datetime.utcnow()
                state_logger.info(f"FAILED(MAX_ROUNDS) | {meeting_id} | {meeting.title} | round={meeting.round_count}")

                # FAILED：仅通知发起人，附带参与者详情
                _notify_failed_initiator_only(meeting, logs, "已达最大协商轮数限制", db)

        elif result.decision_status == DecisionStatus.FAILED:
            # ====== 场景 C: 彻底失败 → FAILED ======
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

            # FAILED：仅通知发起人，附带参与者详情
            logs = db.query(NegotiationLog).filter(
                NegotiationLog.meeting_id == meeting_id
            ).all()
            _notify_failed_initiator_only(meeting, logs, result.agent_reasoning, db)

        db.commit()

        return APIResponse(
            code=200,
            message="协调结果已成功应用，系统状态已更新",
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
    FAILED 时仅通知发起人，附带所有参与者的详细时间信息和拒绝原因。
    被邀请人暂不通知（等发起人决策：取消→OVER 或 重新发起→COLLECTING）。
    """
    # 构建参与者详情
    participants_info = []
    for log in logs:
        user = db.query(User).filter(User.id == log.user_id).first()
        email = user.email if user else "unknown"
        if log.preference_note and log.preference_note.startswith("[已拒绝]"):
            status = f"❌ 已拒绝（{log.preference_note.replace('[已拒绝] ', '')}）"
        elif not log.latest_slots:
            status = "未提交"
        else:
            slots_preview = ", ".join(log.latest_slots[:3])
            status = f"可用: {slots_preview}"
        participants_info.append(f"  {email}: {status}")
    detail = "\n".join(participants_info)

    for log in logs:
        if log.user_id == meeting.initiator_id:
            log.action_required = True
            log.counter_proposal_message = (
                f"❌ 协商失败\n"
                f"会议：{meeting.title}\n"
                f"原因：{reason}\n"
                f"参与者情况：\n{detail}\n\n"
                f"您可以选择：\n"
                f"  · 取消会议（拒绝）\n"
                f"  · 调整时间后重新发起"
            )
        else:
            # 被邀请人暂不通知，等发起人决策
            log.action_required = False
            log.counter_proposal_message = None
        log.suggested_slots = None
        log.updated_at = datetime.utcnow()


def _format_slots_for_agent(slots: list) -> list:
    """
    将数据库中的时间槽格式转换为 Agent 期望的 {start, end} 字典格式

    输入可能是:
      - 字符串 "2026-03-18 14:00-18:00" → {"start": "2026-03-18 14:00", "end": "2026-03-18 18:00"}
      - 字典 {"start": "...", "end": "..."} → 原样返回
    """
    formatted = []
    for slot in slots:
        if isinstance(slot, dict) and "start" in slot and "end" in slot:
            formatted.append(slot)
        elif isinstance(slot, str) and "-" in slot:
            # 尝试解析 "2026-03-18 14:00-18:00" 格式
            # 按最后一个 "-" 之前的日期部分拆分
            parts = slot.rsplit("-", 1)
            if len(parts) == 2:
                start_part = parts[0].strip()
                end_time = parts[1].strip()

                # 提取日期部分（如果 end 只有时间，补上日期）
                date_part = ""
                if " " in start_part:
                    date_part = start_part.split(" ")[0]

                if len(end_time) <= 5 and date_part:
                    # end 只是时间如 "18:00"，需要补日期
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
