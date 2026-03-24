// ============================================================
// Tool 1: BindIdentityTool (bind_identity)
// API 1: POST /api/auth/bind
//
// Registers the user's email and stores the Token locally.
// User says: "bind my email xxx@example.com"
// Agent calls this tool → API call → store Token → return result
// ============================================================

import type { ClawMeetingApiClient } from "../utils/api-client.js";
import { saveCredentials, loadCredentials } from "../utils/storage.js";

/** Tool JSON Schema definition */
export const bindIdentitySchema = {
  name: "bind_identity",
  description: [
    "Bind a user's email to the ClawMeeting meeting negotiation service and obtain an identity Token.",
    "This must be called before using any other plugin features.",
    "If the user has already bound their email, it returns the existing credentials.",
  ].join(" "),
  parameters: {
    type: "object" as const,
    properties: {
      email: {
        type: "string" as const,
        description: "The user's email address for identity registration and binding",
      },
    },
    required: ["email"],
  },
};

/**
 * Tool handler function
 * @param apiClient   - API client instance
 * @param onBindSuccess - Callback after successful binding (currently starts polling)
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

    // 2. Call API to bind
    try {
      const result = await apiClient.bindEmail(email);

      // 3. Persist credentials locally
      saveCredentials({
        email,
        token: result.token,
        user_id: result.user_id,
      });

      // 4. Trigger post-bind flow (start polling, etc.)
      onBindSuccess?.();

      return {
        success: true,
        already_bound: false,
        message: `Binding successful! Email: ${email}, User ID: ${result.user_id}. Token securely stored locally. Background polling started.`,
        user_id: result.user_id,
      };
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      return {
        success: false,
        message: `Identity binding failed: ${errMsg}`,
        hint: "Please verify the server address is correct and the email format is valid.",
      };
    }
  };
}
