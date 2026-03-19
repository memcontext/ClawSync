// ============================================================
// Tool 2: InitiateMeetingTool (initiate_meeting)
// 对应 API 2: POST /api/meetings
//
// 功能: 把用户输入的 "帮我约 B 和 C 开会" 转化为结构化请求
// Agent 负责从用户自然语言中提取参数，然后调用此工具
// 工具会自动附加发起人的可用时间和偏好
// ============================================================

import type { ClawSyncApiClient } from "../utils/api-client.js";
import type { InitiateMeetingRequest } from "../types/index.js";
import {
  getMockAvailableSlotsAsStrings,
  getMockUserPreferences,
  formatPreferencesForAgent,
} from "../utils/mock-calendar.js";

/** Tool 的 JSON Schema 定义 */
export const initiateMeetingSchema = {
  name: "initiate_meeting",
  description: [
    "发起一场新的会议协商。",
    "将用户的约会需求转化为结构化请求发送给服务端，",
    "自动附带发起人的可用时间段和会议偏好。",
    "服务端会通知所有受邀人的 OpenClaw 插件来收集空闲时间。",
    "用户可能说: '帮我约 B 和 C 明天开半小时的架构讨论会'",
  ].join(" "),
  parameters: {
    type: "object" as const,
    properties: {
      title: {
        type: "string" as const,
        description: "会议标题，例如 '项目架构讨论会'",
      },
      duration_minutes: {
        type: "number" as const,
        description: "会议时长（分钟），默认 30",
        default: 30,
      },
      invitees: {
        type: "array" as const,
        items: { type: "string" as const },
        description:
          "受邀人邮箱列表，例如 ['userB@example.com', 'userC@example.com']",
      },
      preference_note: {
        type: "string" as const,
        description:
          "发起人的时间偏好说明（可选），例如 '尽量安排在下午，我不喜欢早会'",
      },
      available_slots: {
        type: "array" as const,
        items: { type: "string" as const },
        description: [
          "发起人的可用时间段列表（可选）。",
          "格式: '2026-03-18 14:00-18:00'。",
          "如果不提供，将自动从日历获取未来3天的空闲时段。",
        ].join(" "),
      },
    },
    required: ["title", "invitees"],
  },
};

/** Tool 的处理函数 */
export function createInitiateMeetingHandler(apiClient: ClawSyncApiClient) {
  return async (params: {
    title: string;
    duration_minutes?: number;
    invitees: string[];
    preference_note?: string;
    available_slots?: string[];
  }) => {
    const {
      title,
      duration_minutes = 30,
      invitees,
      preference_note,
      available_slots,
    } = params;

    // 1. 检查 Token 是否已设置
    if (!apiClient.getToken()) {
      return {
        success: false,
        message: "尚未完成身份绑定，请先调用 bind_identity 工具绑定邮箱。",
      };
    }

    // 2. 获取可用时间段（优先用户指定，否则从 mock 日历获取）
    // API 2 的 initiator_data.available_slots 为字符串数组格式
    const slots = available_slots?.length
      ? available_slots
      : getMockAvailableSlotsAsStrings();

    // 3. 获取用户偏好并合并 preference_note
    const userPrefs = getMockUserPreferences();
    const prefsContext = formatPreferencesForAgent(userPrefs);
    const finalNote = preference_note
      ? `${preference_note}\n\n${prefsContext}`
      : prefsContext;

    // 4. 构造请求
    const requestData: InitiateMeetingRequest = {
      title,
      duration_minutes,
      invitees,
      initiator_data: {
        available_slots: slots,
        preference_note: finalNote,
      },
    };

    // 5. 调用 API 2
    try {
      const result = await apiClient.initiateMeeting(requestData);

      return {
        success: true,
        message: `会议协商已发起！`,
        meeting_id: result.id,
        title: result.title,
        status: result.status,
        details: {
          title,
          duration_minutes,
          invitees,
          available_slots_count: slots.length,
          preference_note: finalNote,
        },
        next_step:
          "服务端将通知受邀人的 OpenClaw 插件，等待他们提交空闲时间。您可以稍后通过轮询查看协商进展。",
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `发起会议失败: ${errMsg}`,
        hint: "请确认服务端运行正常，且所有受邀人邮箱格式正确。",
      };
    }
  };
}
