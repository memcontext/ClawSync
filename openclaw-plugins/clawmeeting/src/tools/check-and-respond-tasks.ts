// ============================================================
// Tool 3: CheckAndRespondTaskTool (check_and_respond_tasks)
// 对应 API 6 + API 5:
//   GET  /api/tasks/pending          — 拉取待办任务
//   POST /api/meetings/{id}/submit   — 提交响应
//
// 核心工作流:
//   轮询拉取任务 → 根据 task_type 分发处理:
//   INITIAL_SUBMIT     → 自动提交空闲时间（后台静默完成）
//   COUNTER_PROPOSAL   → 通知用户，等用户决策后通过此 Tool 提交
//
// Agent 使用本 Tool 的两种模式:
//   模式 A: 无参数调用 → 拉取待办任务列表
//   模式 B: 带参数调用 → 对特定会议提交响应（4种 response_type）
//
// 格式严格对齐服务端 schemas.py ResponseType 枚举
// ============================================================

import type { ClawMeetingApiClient } from "../utils/api-client.js";
import type {
  PendingTask,
  ResponseType,
  TimeSlot,
} from "../types/index.js";


/** Tool 的 JSON Schema 定义 */
export const checkAndRespondTasksSchema = {
  name: "check_and_respond_tasks",
  description: [
    "检查并响应待办的会议协商任务。",
    "",
    "模式 A - 查看待办（无参数调用）：",
    "  拉取服务端待办任务列表，返回每个任务的详情。你需要结合对用户的记忆和日历来处理。",
    "",
    "模式 B - 提交响应（带参数调用）：",
    "  对特定会议提交响应。必须提供 meeting_id 和 response_type。",
    "",
    "response_type 说明：",
    "  INITIAL          — 首次提交空闲时间（需要 available_slots）",
    "  NEW_PROPOSAL     — 协商中重新提交时间（需要 available_slots）",
    "  ACCEPT_PROPOSAL  — 接受协调方的妥协建议（不需要 available_slots）",
    "  REJECT           — 拒绝方案，会议终止（不需要 available_slots）",
    "",
    "工作流程：",
    "  1. 收到 INITIAL_SUBMIT 时，根据你对用户的记忆和日历选择空闲时间提交",
    "  2. 收到 [ClawMeeting 协商通知] 时，协调方的妥协建议已推送给你",
    "  3. 将建议内容告知用户，等用户决定：",
    "     - 用户同意 → 调用本工具，response_type='ACCEPT_PROPOSAL'",
    "     - 用户想改时间 → 调用本工具，response_type='NEW_PROPOSAL' + available_slots",
    "     - 用户拒绝 → 调用本工具，response_type='REJECT'",
  ].join("\n"),
  parameters: {
    type: "object" as const,
    properties: {
      meeting_id: {
        type: "string" as const,
        description:
          "要响应的会议 ID。不提供则仅拉取任务列表。",
      },
      response_type: {
        type: "string" as const,
        enum: ["INITIAL", "NEW_PROPOSAL", "ACCEPT_PROPOSAL", "REJECT"],
        description: [
          "响应类型（提供 meeting_id 时必填）：",
          "INITIAL - 首次提交空闲时间；",
          "NEW_PROPOSAL - 协商中重新提交时间；",
          "ACCEPT_PROPOSAL - 接受协调方建议；",
          "REJECT - 拒绝方案，会议终止。",
        ].join(" "),
      },
      available_slots: {
        type: "array" as const,
        items: {
          type: "object" as const,
          properties: {
            start: {
              type: "string" as const,
              description: "时间段开始，格式: '2026-03-18 14:00'",
            },
            end: {
              type: "string" as const,
              description: "时间段结束，格式: '2026-03-18 16:00'",
            },
          },
          required: ["start", "end"],
        },
        description:
          "可用时间段列表（INITIAL 和 NEW_PROPOSAL 时必填）。Agent 传入 {start, end} 对象，工具内部会转为服务端要求的字符串格式。",
      },
      preference_note: {
        type: "string" as const,
        description: "用户的偏好说明或备注（可选）",
      },
    },
    required: [],
  },
};

// ---- 内部: 构建 INITIAL_SUBMIT 任务的返回信息 ----
function buildInitialSubmitInfo(task: PendingTask): object {
  // 构建发起人时间展示文本
  const initiatorSlots = (task as any).initiator_slots ?? [];
  const slotsDisplay = initiatorSlots.length > 0
    ? `发起人提议的时间段：${initiatorSlots.join("、")}`
    : "发起人未提供具体时间段";

  return {
    meeting_id: task.meeting_id,
    title: task.title,
    action: "NEEDS_AGENT_ACTION",
    task_type: "INITIAL_SUBMIT",
    initiator: task.initiator,
    duration_minutes: task.duration_minutes ?? null,
    round_count: task.round_count ?? 0,
    initiator_slots: initiatorSlots,
    server_message: task.message,
    display_to_user: [
      `📅 收到会议邀请「${task.title}」`,
      `发起人：${task.initiator}`,
      `时长：${task.duration_minutes ?? "未知"} 分钟`,
      slotsDisplay,
      "请告诉我你哪些时间段有空，我帮你提交。",
    ].join("\n"),
    instruction: [
      "收到会议邀请，需要提交空闲时间。",
      "【重要】请先将 display_to_user 的内容展示给用户，包括发起人提议的时间段。",
      "然后根据你对用户的记忆和用户的日历，选择合适的空闲时间段。",
      "记忆中不仅有开会偏好和习惯，还可能有用户提到过的日程安排",
      "（如出差、看病、接送孩子、约饭等），这些未必在日历上，请一并考虑避开。",
      "然后调用本工具提交：meeting_id、response_type='INITIAL'、available_slots。",
      "如果你不清楚用户的空闲时间，请直接询问用户。",
    ].join(""),
  };
}

// ---- 内部: 构建 COUNTER_PROPOSAL 任务的返回信息 ----
function buildCounterProposalInfo(task: PendingTask): object {
  const suggestedSlots = (task as any).suggested_slots ?? [];
  const slotsText = suggestedSlots.length > 0
    ? `建议时间段：${suggestedSlots.join("、")}`
    : "";

  return {
    meeting_id: task.meeting_id,
    title: task.title,
    action: "NEEDS_USER_DECISION",
    task_type: "COUNTER_PROPOSAL",
    initiator: task.initiator,
    duration_minutes: task.duration_minutes ?? null,
    round_count: task.round_count ?? 0,
    suggested_slots: suggestedSlots,
    coordinator_message: task.message,
    display_to_user: [
      `🔄 会议「${task.title}」需要协商（第 ${task.round_count ?? 0} 轮）`,
      `协调建议：${task.message}`,
      slotsText,
      "",
      "你可以选择：接受建议 / 提交新时间 / 拒绝会议",
    ].filter(Boolean).join("\n"),
    instruction: [
      "协调方发来了协商建议，需要用户决策。",
      "【重要】请先将 display_to_user 的内容展示给用户，包括建议的时间段。",
      "结合你对用户的记忆（偏好习惯及用户提到过的日程安排）和用户日历情况供参考。",
      "然后等用户决定：",
      "  - 用户同意建议 → 调用本工具，meeting_id + response_type='ACCEPT_PROPOSAL'",
      "  - 用户想改时间 → 调用本工具，meeting_id + response_type='NEW_PROPOSAL' + available_slots",
      "  - 用户拒绝 → 调用本工具，meeting_id + response_type='REJECT'",
    ].join("\n"),
  };
}

/** Tool 的处理函数 */
export function createCheckAndRespondTasksHandler(
  apiClient: ClawMeetingApiClient,
) {
  return async (params: {
    meeting_id?: string;
    response_type?: ResponseType;
    available_slots?: TimeSlot[];
    preference_note?: string;
  }) => {
    const { meeting_id, response_type, available_slots, preference_note } =
      params;

    // 检查 Token
    if (!apiClient.getToken()) {
      return {
        success: false,
        message: "尚未完成身份绑定，请先调用 bind_identity 工具绑定邮箱。",
      };
    }

    // =============================================
    // 模式 B: 提交对特定会议的响应
    // =============================================
    if (meeting_id && response_type) {
      // ACCEPT_PROPOSAL 和 REJECT 不需要 available_slots
      if ((response_type === "INITIAL" || response_type === "NEW_PROPOSAL") && !available_slots?.length) {
        return {
          success: false,
          message: `response_type='${response_type}' 需要提供 available_slots。`,
        };
      }

      // 将 {start, end} 对象转为服务端要求的字符串格式
      const slotsAsStrings = (available_slots ?? []).map((slot) => {
        if (typeof slot === "string") return slot;
        const startDate = slot.start.split(" ")[0] ?? "";
        const startTime = slot.start.split(" ")[1] ?? slot.start;
        const endTime = slot.end.split(" ")[1] ?? slot.end;
        return `${startDate} ${startTime}-${endTime}`;
      });

      const submitData = {
        response_type,
        available_slots: slotsAsStrings,
        preference_note,
      };

      try {
        const result = await apiClient.submitAvailability(
          meeting_id,
          submitData,
        );
        return {
          success: true,
          meeting_id,
          response_type,
          message: response_type === "ACCEPT_PROPOSAL"
            ? "已接受协调方建议。"
            : response_type === "REJECT"
              ? "已拒绝方案，会议协商终止。"
              : "响应已提交。",
          status: result.status,
          all_submitted: result.all_submitted,
        };
      } catch (error: unknown) {
        const errMsg = error instanceof Error ? error.message : String(error);
        return {
          success: false,
          meeting_id,
          message: `提交响应失败: ${errMsg}`,
        };
      }
    }

    // =============================================
    // 模式 A: 拉取待办任务，返回给 Agent 处理
    // =============================================
    try {
      const { pending_tasks } = await apiClient.getPendingTasks();

      if (!pending_tasks || pending_tasks.length === 0) {
        return {
          success: true,
          message: "当前没有待处理的会议协商任务。",
          pending_count: 0,
        };
      }

      const results: object[] = [];

      for (const task of pending_tasks) {
        switch (task.task_type) {
          case "INITIAL_SUBMIT": {
            // Plugin 团队方案：额外调 getMeetingDetail 获取所有参与者的 latest_slots
            let meetingDetail: any = null;
            try {
              meetingDetail = await apiClient.getMeetingDetail(task.meeting_id);
            } catch (e) {
              console.log(`[ClawMeeting] 拉详情失败，退化为基础信息: ${e}`);
            }

            const info = buildInitialSubmitInfo(task) as any;

            if (meetingDetail?.participants) {
              // 把已提交方的 latest_slots 塞进返回值，agent 可以更智能地选重叠时间
              info.participants_slots = meetingDetail.participants
                .filter((p: any) => p.latest_slots && p.latest_slots.length > 0)
                .map((p: any) => ({
                  email: p.email,
                  role: p.role,
                  latest_slots: p.latest_slots,
                }));
            }

            results.push(info);
            break;
          }
          case "COUNTER_PROPOSAL": {
            // Plugin 团队方案：额外调 getMeetingDetail 获取协调推理和各方时间
            let meetingDetail: any = null;
            try {
              meetingDetail = await apiClient.getMeetingDetail(task.meeting_id);
            } catch (e) {
              console.log(`[ClawMeeting] 拉详情失败，退化为基础信息: ${e}`);
            }

            const info = buildCounterProposalInfo(task) as any;

            if (meetingDetail) {
              info.coordinator_reasoning = meetingDetail.coordinator_reasoning ?? null;
              info.participants_slots = (meetingDetail.participants ?? [])
                .filter((p: any) => p.latest_slots && p.latest_slots.length > 0)
                .map((p: any) => ({
                  email: p.email,
                  role: p.role,
                  latest_slots: p.latest_slots,
                }));
            }

            results.push(info);
            break;
          }
          case "MEETING_CONFIRMED":
            results.push({
              meeting_id: task.meeting_id,
              title: task.title,
              action: "NOTIFY_USER",
              task_type: "MEETING_CONFIRMED",
              initiator: task.initiator,
              display_to_user: task.message,
              instruction: "会议已确认！请将 display_to_user 的内容完整展示给用户。",
            });
            break;
          case "MEETING_FAILED":
            results.push({
              meeting_id: task.meeting_id,
              title: task.title,
              action: "NOTIFY_USER",
              task_type: "MEETING_FAILED",
              initiator: task.initiator,
              display_to_user: task.message,
              instruction: "会议协商失败。请将 display_to_user 的内容完整展示给用户。",
            });
            break;
          case "MEETING_CONFIRMED":
            results.push({
              meeting_id: task.meeting_id,
              title: task.title,
              action: "NOTIFY_USER",
              task_type: task.task_type,
              initiator: task.initiator,
              message: task.message,
              instruction: "会议已确认，请将最终时间通知用户。",
            });
            break;
          case "MEETING_FAILED":
            results.push({
              meeting_id: task.meeting_id,
              title: task.title,
              action: "NOTIFY_USER",
              task_type: task.task_type,
              initiator: task.initiator,
              message: task.message,
              instruction: "会议协商失败，请通知用户。",
            });
            break;
          default:
            console.log(
              `[ClawMeeting] 未识别的 task_type: "${task.task_type}", meeting_id=${task.meeting_id}`,
            );
            results.push({
              meeting_id: task.meeting_id,
              title: task.title,
              action: "NOTIFY_USER",
              task_type: task.task_type,
              initiator: task.initiator,
              display_to_user: task.message,
              instruction: `请将 display_to_user 的内容通知用户。`,
            });
            break;
        }
      }

      return {
        success: true,
        pending_count: pending_tasks.length,
        message: `共 ${pending_tasks.length} 个待办任务，请逐一处理。`,
        task_results: results,
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `获取待办任务失败: ${errMsg}`,
        hint: "请检查网络连接和服务端状态。",
      };
    }
  };
}
