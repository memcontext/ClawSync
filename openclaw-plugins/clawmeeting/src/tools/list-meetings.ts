// ============================================================
// Tool 4: ListMeetingsTool (list_meetings)
// 对应 API 3 + API 4:
//   GET /api/meetings              — 我的会议列表
//   GET /api/meetings/{meeting_id} — 查询会议详情
//
// 用户说: "查看我的会议" / "会议详情" / "xxx会议怎么样了"
// Agent 调用此工具查看会议列表或指定会议的详情
//
// 对齐 API_REFERENCE.md v1.0.0
// ============================================================

import type { ClawMeetingApiClient } from "../utils/api-client.js";

/** Tool 的 JSON Schema 定义 */
export const listMeetingsSchema = {
  name: "list_meetings",
  description: [
    "查看用户参与的会议列表或指定会议的详情。",
    "",
    "模式 A - 不传 meeting_id：返回用户参与的所有会议（含发起的和被邀请的），按创建时间倒序。",
    "模式 B - 传入 meeting_id：返回该会议的详细信息，包括各参与者的提交状态、协调推理等。",
    "",
    "用户可能说：",
    "  - '查看我的会议'",
    "  - 'xxx 会议怎么样了'",
    "  - '会议详情'",
  ].join("\n"),
  parameters: {
    type: "object" as const,
    properties: {
      meeting_id: {
        type: "string" as const,
        description: "会议 ID（可选）。不传则返回会议列表，传入则返回该会议详情。",
      },
    },
    required: [],
  },
};

/** Tool 的处理函数 */
export function createListMeetingsHandler(apiClient: ClawMeetingApiClient) {
  return async (params: { meeting_id?: string }) => {
    const { meeting_id } = params;

    if (!apiClient.getToken()) {
      return {
        success: false,
        message: "尚未完成身份绑定，请先调用 bind_identity 工具绑定邮箱。",
      };
    }

    // 模式 B: 查询指定会议详情
    if (meeting_id) {
      try {
        const detail = await apiClient.getMeetingDetail(meeting_id);
        return {
          success: true,
          meeting: detail,
        };
      } catch (error: unknown) {
        const errMsg = error instanceof Error ? error.message : String(error);
        return {
          success: false,
          message: `查询会议详情失败: ${errMsg}`,
        };
      }
    }

    // 模式 A: 查询会议列表
    try {
      const list = await apiClient.getMeetingList();
      return {
        success: true,
        total: list.total,
        meetings: list.meetings,
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `查询会议列表失败: ${errMsg}`,
      };
    }
  };
}
