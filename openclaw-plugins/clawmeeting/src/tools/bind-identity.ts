// ============================================================
// Tool 1: BindIdentityTool (bind_identity)
// Step 1: POST /api/auth/send-code — 发送验证码到用户邮箱
//
// User says: "绑定邮箱 xxx@example.com"
// Agent calls this tool → 发送验证码 → 提示用户回复验证码
// 然后用户回复验证码 → Agent 调用 verify_email_code 完成绑定
// ============================================================

import type { ClawMeetingApiClient } from "../utils/api-client.js";
import { loadCredentials } from "../utils/storage.js";

/** Tool JSON Schema definition */
export const bindIdentitySchema = {
  name: "bind_identity",
  description: [
    "[ClawMeeting Plugin Tool] Send a verification code to the user's email to start the binding process.",
    "This is Step 1 of email binding. After the user receives and replies with the code,",
    "call verify_email_code to complete the binding.",
    "If the user has already bound their email, it returns the existing credentials.",
    "IMPORTANT: Always use this tool for email binding — never call any external API directly.",
  ].join(" "),
  parameters: {
    type: "object" as const,
    properties: {
      email: {
        type: "string" as const,
        description: "The user's email address to send the verification code to",
      },
    },
    required: ["email"],
  },
};

/**
 * Tool handler function
 * @param apiClient   - API client instance
 * @param onBindSuccess - Callback after successful binding (starts polling)
 */
export function createBindIdentityHandler(
  apiClient: ClawMeetingApiClient,
  onBindSuccess?: () => void,
) {
  return async (params: { email: string }) => {
    const { email } = params;

    // 1. Check if already bound
    const existing = loadCredentials();
    if (existing?.token && existing.email === email) {
      apiClient.setToken(existing.token);
      onBindSuccess?.();

      return {
        success: true,
        already_bound: true,
        message: `Email (${email}) is already bound. Token loaded. Background polling started.`,
        user_id: existing.user_id,
      };
    }

    // 2. Send verification code
    try {
      const result = await apiClient.sendVerificationCode(email);

      return {
        success: true,
        already_bound: false,
        step: "verification_code_sent",
        message: result.message,
        hint: `Verification code sent to ${email}. Ask the user to check their email and reply with the 6-digit code. Then call verify_email_code with the email and code to complete binding.`,
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `Failed to send verification code: ${errMsg}`,
        hint: "Please verify the server address is correct and the email format is valid.",
      };
    }
  };
}
