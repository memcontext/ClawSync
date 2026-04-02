# Plugin — Email Verification Binding Changes

## Background

The plugin's `bind_identity` tool previously called `POST /api/auth/bind` directly (no verification).
It has been changed to a two-step verification code flow to ensure the user actually owns the email address.

## Modified Files

### 1. `src/utils/api-client.ts`

**Two new methods:**

```typescript
// Send verification code
async sendVerificationCode(email: string): Promise<{ message: string }> {
  const res = await this.request<{ message: string }>(
    "POST",
    "/api/auth/send-code",
    { email },
  );
  return res.data!;
}

// Verify code + bind
async verifyAndBind(email: string, code: string): Promise<BindAuthResponse> {
  const res = await this.request<BindAuthResponse>(
    "POST",
    "/api/auth/verify-bind",
    { email, code },
  );
  if (res.data?.token) {
    this.setToken(res.data.token);
  }
  return res.data!;
}
```

**Note:** The `request()` method's error handling for `code !== 200` needs adjustment. The send-code endpoint returns HTTP 200 with `code=429` when rate-limited, and verify-bind returns HTTP 200 with `code=400` on verification failure. These two methods should handle non-200 code values individually, returning the message as a user-facing hint rather than throwing an exception.

### 2. `src/tools/bind-identity.ts`

**Changed to a two-step interaction flow:**

Previous flow (single step):
```
User: "Bind my email xxx@qq.com"
-> Call apiClient.bindEmail(email)
-> Returns token, binding complete
```

New flow (two steps):
```
User: "Bind my email xxx@qq.com"
-> Step 1: Call apiClient.sendVerificationCode(email)
-> Returns: "Verification code sent to xxx@qq.com, please check your inbox and reply with the code"

User: "The code is 123456"
-> Step 2: Call apiClient.verifyAndBind(email, "123456")
-> Success: Save token, binding complete
-> Failure: "Invalid code, please re-enter or request a new one"
```

**Implementation options:**

#### Option A: Split into two tools (recommended)

Add a `verify_email_code` tool to work alongside `bind_identity`:

- **`bind_identity`**: Receives email -> calls send-code -> returns "please reply with code"
- **`verify_email_code`**: Receives email + code -> calls verify-bind -> saves token

```typescript
// New file: verify-email-code.ts
export const verifyEmailCodeSchema = {
  name: "verify_email_code",
  description: "Verify the email code sent to the user's email to complete binding.",
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
```

#### Option B: Single tool with optional parameter

Add an optional `code` parameter to the existing `bind_identity`:

- No code -> send verification code (Step 1)
- With code -> verify and bind (Step 2)

```typescript
export const bindIdentitySchema = {
  name: "bind_identity",
  description: "Bind email with verification. Call without code to send verification email, call with code to complete binding.",
  parameters: {
    type: "object" as const,
    properties: {
      email: {
        type: "string" as const,
        description: "The user's email address",
      },
      code: {
        type: "string" as const,
        description: "Optional: 6-digit verification code from email. Omit to send code first.",
      },
    },
    required: ["email"],
  },
};
```

### 3. `index.ts`

If using Option A (two tools), register the new `verify_email_code` tool in `index.ts`.

### 4. `src/types/index.ts`

New type definitions:

```typescript
export interface SendCodeRequest {
  email: string;
}

export interface VerifyBindRequest {
  email: string;
  code: string;
}
```

## Files Not Modified

- `src/utils/storage.ts` — Storage logic unchanged
- `src/utils/polling-manager.ts` — Polling logic unchanged
- `src/tools/initiate-meeting.ts` — Not affected
- `src/tools/check-and-respond-tasks.ts` — Not affected
- `src/tools/list-meetings.ts` — Not affected

## Interaction Examples

### Normal Flow
```
User: Bind my email test@example.com
Bot:  A verification code has been sent to test@example.com. Please check your inbox and reply with the 6-digit code.

User: The code is 385721
Bot:  Email binding successful! You're now logged in and can start using meeting features.
```

### Invalid Code
```
User: The code is 000000
Bot:  Invalid verification code. Please double-check and try again. To request a new code, say "resend verification code".
```

### Rate Limited
```
User: Resend verification code
Bot:  Please wait 45 seconds before trying again.
```
