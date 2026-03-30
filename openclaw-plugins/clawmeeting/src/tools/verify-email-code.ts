// ============================================================
// Tool 5: VerifyEmailCodeTool (verify_email_code)
// Step 2: POST /api/auth/verify-bind — 验证码校验 + 绑定注册
//
// User replies: "验证码是 385721"
// Agent calls this tool → 验证码校验 → 保存 Token → 绑定完成
// ============================================================

import type { ClawMeetingApiClient } from "../utils/api-client.js";
import { saveCredentials } from "../utils/storage.js";

/** Tool JSON Schema definition */
export const verifyEmailCodeSchema = {
  name: "verify_email_code",
  description: [
    "[ClawMeeting Plugin Tool] Verify the email code to complete email binding.",
    "This is Step 2 of email binding. Must be called after bind_identity has sent the verification code.",
    "On success, the user's token is stored locally and background polling starts automatically.",
    "IMPORTANT: Always use this tool to verify codes — never call any external API directly.",
  ].join(" "),
  parameters: {
    type: "object" as const,
    properties: {
      email: {
        type: "string" as const,
        description: "The email address that received the verification code",
      },
      code: {
        type: "string" as const,
        description: "The 6-digit verification code from the email",
      },
    },
    required: ["email", "code"],
  },
};

/**
 * Tool handler function
 * @param apiClient     - API client instance
 * @param onBindSuccess - Callback after successful binding (starts polling)
 */
export function createVerifyEmailCodeHandler(
  apiClient: ClawMeetingApiClient,
  onBindSuccess?: () => void,
) {
  return async (params: { email: string; code: string }) => {
    const { email, code } = params;

    try {
      const result = await apiClient.verifyAndBind(email, code);

      if (!result.success || !result.data) {
        return {
          success: false,
          message: result.message,
          hint: "Verification code is incorrect or expired. Ask the user to re-enter the code or call bind_identity again to resend a new code.",
        };
      }

      // Persist credentials locally
      saveCredentials({
        email,
        token: result.data.token,
        user_id: result.data.user_id,
      });

      // Trigger post-bind flow (start polling, etc.)
      onBindSuccess?.();

      return {
        success: true,
        message: `Email binding successful! Email: ${email}, User ID: ${result.data.user_id}. Token securely stored locally. Background polling started.`,
        user_id: result.data.user_id,
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `Verification failed: ${errMsg}`,
        hint: "Please check the verification code and try again.",
      };
    }
  };
}
