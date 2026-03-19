// ============================================================
// Tool 1: BindIdentityTool (bind_identity)
// 对应 API 1: POST /api/auth/bind
//
// 功能: 完成身份注册并把 Token 存入本地配置
// 用户说: "帮我绑定邮箱 xxx@example.com"
// Agent 调用此工具 → 调 API → 存 Token → 返回结果
// ============================================================

import type { ClawSyncApiClient } from "../utils/api-client.js";
import { saveCredentials, loadCredentials } from "../utils/storage.js";

/** Tool 的 JSON Schema 定义 */
export const bindIdentitySchema = {
  name: "bind_identity",
  description: [
    "将用户邮箱绑定到 ClawSync 会议协商服务，获取身份 Token。",
    "首次使用本插件时必须先调用此工具完成身份注册。",
    "如果用户已经绑定过，会返回已有的凭证信息。",
  ].join(" "),
  parameters: {
    type: "object" as const,
    properties: {
      email: {
        type: "string" as const,
        description: "用户的邮箱地址，用于身份注册与绑定",
      },
    },
    required: ["email"],
  },
};

/**
 * Tool 的处理函数
 * @param apiClient   - API 客户端实例
 * @param onBindSuccess - [扩展点] 绑定成功后的回调，当前用于启动轮询
 *                        后续可扩展为触发其他初始化流程
 */
export function createBindIdentityHandler(
  apiClient: ClawSyncApiClient,
  onBindSuccess?: () => void,
) {
  return async (params: { email: string }) => {
    const { email } = params;

    // 1. 检查是否已绑定
    const existing = loadCredentials();
    if (existing?.token && existing.email === email) {
      // 已绑定，恢复 Token 到 client
      apiClient.setToken(existing.token);

      // 即使是已绑定的情况，也确保轮询已启动
      // （覆盖场景：用户重启了 OpenClaw 后再次说"绑定邮箱"）
      onBindSuccess?.();

      return {
        success: true,
        already_bound: true,
        message: `该邮箱 (${email}) 已绑定，Token 已加载。无需重复绑定。后台任务轮询已启动。`,
        user_id: existing.user_id,
      };
    }

    // 2. 调用 API 1 进行绑定
    try {
      const result = await apiClient.bindEmail(email);

      // 3. 持久化到本地
      saveCredentials({
        email,
        token: result.token,
        user_id: result.user_id,
      });

      // 4. 绑定成功 → 触发后续流程（启动轮询等）
      onBindSuccess?.();

      return {
        success: true,
        already_bound: false,
        message: `绑定成功！邮箱: ${email}，用户ID: ${result.user_id}。Token 已安全存储到本地，后台任务轮询已启动。`,
        user_id: result.user_id,
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `身份绑定失败: ${errMsg}`,
        hint: "请检查服务端地址是否正确，以及邮箱格式是否有效。",
      };
    }
  };
}
