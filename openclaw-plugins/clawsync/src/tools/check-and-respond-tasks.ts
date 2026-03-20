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

import type { ClawSyncApiClient } from "../utils/api-client.js";
import type {
  PendingTask,
  ResponseType,
  TimeSlot,
} from "../types/index.js";
import {
  getMockAvailableSlots,
  getMockUserPreferences,
  formatPreferencesForAgent,
} from "../utils/mock-calendar.js";

/** Tool 的 JSON Schema 定义 */
export const checkAndRespondTasksSchema = {
  name: "check_and_respond_tasks",
  description: [
    "检查并响应待办的会议协商任务。",
    "",
    "模式 A - 查看待办（无参数调用）：",
    "  拉取服务端待办任务列表，返回每个任务的详情、用户日历空闲时段和偏好。",
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
    "  1. 后台轮询自动处理 INITIAL_SUBMIT（静默提交空闲时间）",
    "  2. 收到 [ClawSync 协商通知] 时，协调方的妥协建议已推送给你",
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
  const slots = getMockAvailableSlots();
  const prefs = getMockUserPreferences();

  return {
    meeting_id: task.meeting_id,
    title: task.title,
    action: "NEEDS_AGENT_ACTION",
    task_type: "INITIAL_SUBMIT",
    initiator: task.initiator,
    duration_minutes: task.duration_minutes ?? null,
    round_count: task.round_count ?? 0,
    server_message: task.message,
    calendar_available_slots: slots,
    user_preferences: formatPreferencesForAgent(prefs),
    instruction: [
      "收到会议邀请，需要提交空闲时间。",
      "上面的 calendar_available_slots 是用户日历中的可用时段，",
      "user_preferences 是用户的开会偏好。",
      "请根据这些信息，再次调用本工具并传入：",
      "meeting_id、response_type 设为 'INITIAL'、",
      "以及从日历数据中选择合适的 available_slots。",
    ].join(""),
  };
}

// ---- 内部: 构建 COUNTER_PROPOSAL 任务的返回信息 ----
function buildCounterProposalInfo(task: PendingTask): object {
  const slots = getMockAvailableSlots();
  const prefs = getMockUserPreferences();

  return {
    meeting_id: task.meeting_id,
    title: task.title,
    action: "NEEDS_USER_DECISION",
    task_type: "COUNTER_PROPOSAL",
    initiator: task.initiator,
    duration_minutes: task.duration_minutes ?? null,
    round_count: task.round_count ?? 0,
    coordinator_message: task.message,
    calendar_available_slots: slots,
    user_preferences: formatPreferencesForAgent(prefs),
    instruction: [
      "协调方发来了协商建议，需要用户决策。",
      "请将 coordinator_message 内容告知用户，同时展示用户当前的日历空闲时段供参考。",
      "然后等用户决定：",
      "  - 用户同意建议 → 调用本工具，meeting_id + response_type='ACCEPT_PROPOSAL'",
      "  - 用户想改时间 → 调用本工具，meeting_id + response_type='NEW_PROPOSAL' + available_slots",
      "  - 用户拒绝 → 调用本工具，meeting_id + response_type='REJECT'",
    ].join("\n"),
  };
}

/** Tool 的处理函数 */
export function createCheckAndRespondTasksHandler(
  apiClient: ClawSyncApiClient,
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
          case "INITIAL_SUBMIT":
            results.push(buildInitialSubmitInfo(task));
            break;
          case "COUNTER_PROPOSAL":
            results.push(buildCounterProposalInfo(task));
            break;
          default:
            console.log(
              `[ClawSync] 未识别的 task_type: "${task.task_type}", meeting_id=${task.meeting_id}`,
            );
            results.push({
              meeting_id: task.meeting_id,
              title: task.title,
              action: "NOTIFY_USER",
              task_type: task.task_type,
              initiator: task.initiator,
              message: task.message,
              instruction: `未知任务类型 ${task.task_type}，请将消息通知用户。`,
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
