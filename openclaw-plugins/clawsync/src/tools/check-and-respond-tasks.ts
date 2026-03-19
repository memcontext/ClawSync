// ============================================================
// Tool 3: CheckAndRespondTaskTool (check_and_respond_tasks)
// 对应 API 6 + API 5:
//   GET  /api/tasks/pending          — 拉取待办任务
//   POST /api/meetings/{id}/submit   — 提交空闲时间
//
// 核心工作流:
//   轮询拉取任务 → 所有任务类型都交给 Agent 处理:
//   INITIAL_SUBMIT     → Agent 读取日历，组装时间段，调用本 Tool 提交
//   COUNTER_PROPOSAL   → Agent 展示协调方建议，等用户决策后调用本 Tool 提交
//
// Agent 使用本 Tool 的两种模式:
//   模式 A: 无参数调用 → 拉取待办任务列表
//   模式 B: 带参数调用 → 对特定会议提交响应
//
// 格式严格对齐 API_REFERENCE.md v1.0.0
// ============================================================

import type { ClawSyncApiClient } from "../utils/api-client.js";
import type {
  PendingTask,
  ResponseType,
  TimeSlot,
  SubmitAvailabilityRequest,
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
    "  Agent 应根据返回信息决定下一步操作。",
    "",
    "模式 B - 提交响应（带参数调用）：",
    "  对特定会议提交空闲时间。需要提供 meeting_id、response_type 和 available_slots。",
    "",
    "工作流程：",
    "  1. 先无参数调用，获取待办任务和用户日历数据",
    "  2. 对 INITIAL_SUBMIT 任务：根据日历数据组装时间段，带参数调用提交",
    "  3. 对 COUNTER_PROPOSAL 任务：展示协调方建议给用户，等用户决定后带参数调用提交",
  ].join("\n"),
  parameters: {
    type: "object" as const,
    properties: {
      meeting_id: {
        type: "string" as const,
        description:
          "要响应的会议 ID（可选）。不提供则仅拉取任务列表。",
      },
      response_type: {
        type: "string" as const,
        enum: ["INITIAL", "COUNTER"],
        description: [
          "响应类型（当提供 meeting_id 时必填）：",
          "INITIAL - 首次提交空闲时间；",
          "COUNTER - 协商轮次中重新提交时间。",
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
          "可用时间段列表。Agent 应根据日历数据填充此字段。",
      },
      preference_note: {
        type: "string" as const,
        description: "用户的偏好说明或对妥协方案的回复（可选）",
      },
    },
    required: [],
  },
};

// ---- 内部: 构建 INITIAL_SUBMIT 任务的返回信息 ----
// 不自动提交，返回日历数据和指令，交给 Agent 处理
function buildInitialSubmitInfo(task: PendingTask): object {
  const slots = getMockAvailableSlots();
  const prefs = getMockUserPreferences();

  return {
    meeting_id: task.meeting_id,
    title: task.title,
    action: "NEEDS_AGENT_ACTION",
    task_type: "INITIAL_SUBMIT",
    initiator: task.initiator,
    server_message: task.message,
    // 提供日历数据供 Agent 使用
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
// 需要用户决策，返回协调方建议
function buildCounterProposalInfo(task: PendingTask): object {
  const slots = getMockAvailableSlots();
  const prefs = getMockUserPreferences();

  return {
    meeting_id: task.meeting_id,
    title: task.title,
    action: "NEEDS_AGENT_ACTION",
    task_type: "COUNTER_PROPOSAL",
    initiator: task.initiator,
    coordinator_message: task.message,
    // 同样提供日历数据供参考
    calendar_available_slots: slots,
    user_preferences: formatPreferencesForAgent(prefs),
    instruction: [
      "协调方发来了新的协商建议。",
      "如果是用户主动查询待办，展示 coordinator_message 和日历空闲时段供参考，等用户决定后提交。",
      "如果是后台自动处理，直接根据日历数据选择合适时间段提交即可，无需通知用户。",
      "调用本工具传入：meeting_id、response_type 设为 'COUNTER'、以及 available_slots。",
    ].join(""),
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
    if (meeting_id && response_type && available_slots?.length) {
      // 服务端要求 available_slots 为字符串数组格式: "2026-03-19 14:00-17:00"
      // Agent 传入的是 {start, end} 对象，这里做格式转换
      const slotsAsStrings = available_slots.map((slot) => {
        if (typeof slot === "string") return slot;
        // 从 "2026-03-19 14:00" 中提取时间部分拼接
        const startTime = slot.start.split(" ")[1] ?? slot.start;
        const endTime = slot.end.split(" ")[1] ?? slot.end;
        const dateStr = slot.start.split(" ")[0] ?? "";
        return `${dateStr} ${startTime}-${endTime}`;
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
          message: `响应已提交。`,
          status: result.status,
          all_submitted: result.all_submitted,
          coordinator_result: result.coordinator_result ?? null,
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

      // 所有任务都交给 Agent，不自动处理
      const results: object[] = [];

      for (const task of pending_tasks) {
        if (task.task_type === "INITIAL_SUBMIT") {
          results.push(buildInitialSubmitInfo(task));
        } else if (task.task_type === "COUNTER_PROPOSAL") {
          results.push(buildCounterProposalInfo(task));
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
