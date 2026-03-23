// ============================================================
// Tool 2: InitiateMeetingTool (initiate_meeting)
// 对应 API 2: POST /api/meetings
//
// 功能: 把用户输入的 "帮我约 B 和 C 开会" 转化为结构化请求
// Agent 负责从用户自然语言中提取所有参数（标题、时长、受邀人、可用时间段），
// 用户只需用自然语言描述，Agent 自行解析为结构化数据后调用此工具。
// ============================================================

import type { ClawMeetingApiClient } from "../utils/api-client.js";
import type { InitiateMeetingRequest } from "../types/index.js";

/** Tool 的 JSON Schema 定义 */
export const initiateMeetingSchema = {
  name: "initiate_meeting",
  description: [
    "发起一场新的会议协商。",
    "将用户的约会需求转化为结构化请求发送给服务端，",
    "服务端会通知所有受邀人的 OpenClaw 插件来收集空闲时间。",
    "",
    "所有参数均为必填。用户会用自然语言描述需求，",
    "例如: '帮我约 bob@x.com 和 charlie@x.com 明天下午2点到5点开一个半小时的架构讨论会'",
    "你需要从中解析出 title、duration_minutes、invitees、available_slots 四个字段。",
    "",
    "如果用户描述中缺少以下任何信息，请主动追问，不要自行假设：",
    "  - 会议时长（duration_minutes）",
    "  - 发起人的可用时间段（available_slots）",
  ].join("\n"),
  parameters: {
    type: "object" as const,
    properties: {
      title: {
        type: "string" as const,
        description: "会议标题，例如 '项目架构讨论会'",
      },
      duration_minutes: {
        type: "number" as const,
        description: "会议时长（分钟），必填。从用户描述中解析，例如 '半小时' → 30，'一个小时' → 60",
      },
      invitees: {
        type: "array" as const,
        items: { type: "string" as const },
        description:
          "受邀人邮箱列表，例如 ['bob@example.com', 'charlie@example.com']",
      },
      available_slots: {
        type: "array" as const,
        items: { type: "string" as const },
        description: [
          "发起人的可用时间段列表，必填。从用户的自然语言描述中解析。",
          "格式: 'YYYY-MM-DD HH:MM-HH:MM'，例如 '2026-03-20 14:00-17:00'。",
          "用户可能说 '明天下午2点到5点'，你需要转换为具体日期和时间。",
          "可以有多个时间段。",
        ].join(" "),
      },
      preference_note: {
        type: "string" as const,
        description:
          "发起人的偏好说明（可选）。仅当你的记忆中确实存在用户的开会偏好时才填写，例如用户说过不喜欢早会、周五下午不开会等。没有相关记忆就不要填，绝对不要编造。也可包含用户本次额外说明的偏好。",
      },
    },
    required: ["title", "duration_minutes", "invitees", "available_slots"],
  },
};

/** Tool 的处理函数 */
export function createInitiateMeetingHandler(apiClient: ClawMeetingApiClient) {
  return async (params: {
    title: string;
    duration_minutes: number;
    invitees: string[];
    available_slots: string[];
    preference_note?: string;
  }) => {
    const {
      title,
      duration_minutes,
      invitees,
      available_slots,
      preference_note,
    } = params;

    // 1. 检查 Token 是否已设置
    if (!apiClient.getToken()) {
      return {
        success: false,
        message: "尚未完成身份绑定，请先调用 bind_identity 工具绑定邮箱。",
      };
    }

    // 2. 校验必填参数
    if (!available_slots || available_slots.length === 0) {
      return {
        success: false,
        message: "缺少可用时间段（available_slots），请从用户描述中解析并提供。",
      };
    }
    if (!duration_minutes || duration_minutes <= 0) {
      return {
        success: false,
        message: "缺少会议时长（duration_minutes），请从用户描述中解析并提供。",
      };
    }

    // 3. preference_note 由 Agent 根据对用户的记忆自行填写
    const finalNote = preference_note ?? undefined;

    // 4. 构造请求
    const requestData: InitiateMeetingRequest = {
      title,
      duration_minutes,
      invitees,
      initiator_data: {
        available_slots,
        preference_note: finalNote,
      },
    };

    // 5. 调用 API 2
    try {
      const result = await apiClient.initiateMeeting(requestData);

      // 6. 校验响应字段
      const errors: string[] = [];
      if (!result.id || typeof result.id !== "string") {
        errors.push("响应缺少 id 字段或格式错误");
      }
      if (!result.status) {
        errors.push("响应缺少 status 字段");
      } else if (result.status !== "COLLECTING") {
        errors.push(`状态异常: 期望 COLLECTING，实际 ${result.status}`);
      }
      if (!result.title || typeof result.title !== "string") {
        errors.push("响应缺少 title 字段");
      }
      if (!result.duration_minutes || result.duration_minutes <= 0) {
        errors.push("响应缺少 duration_minutes 或值无效");
      }
      if (!result.initiator_data) {
        errors.push("响应缺少 initiator_data 字段");
      } else {
        if (!result.initiator_data.available_slots || !Array.isArray(result.initiator_data.available_slots) || result.initiator_data.available_slots.length === 0) {
          errors.push("initiator_data.available_slots 为空或格式错误");
        }
      }
      if (!result.invitees || !Array.isArray(result.invitees) || result.invitees.length === 0) {
        errors.push("响应缺少 invitees 或为空数组");
      }

      if (errors.length > 0) {
        console.log(`[ClawMeeting] initiate_meeting 响应校验警告: ${errors.join("; ")}`);
        return {
          success: false,
          message: `会议创建请求已发送，但响应字段校验不通过: ${errors.join("; ")}`,
          raw_response: result,
        };
      }

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
          available_slots_count: available_slots.length,
          available_slots,
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
