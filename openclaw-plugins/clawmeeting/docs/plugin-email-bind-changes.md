# OpenClaw 插件端 — 邮箱验证绑定改造说明

## 背景

当前插件的 `bind_identity` 工具直接调用 `POST /api/auth/bind` 完成绑定（无验证）。
需要改为两步验证码流程，确保用户真正拥有该邮箱。

## 需要修改的文件

### 1. `src/utils/api-client.ts`

**新增两个方法：**

```typescript
// 发送验证码
async sendVerificationCode(email: string): Promise<{ message: string }> {
  const res = await this.request<{ message: string }>(
    "POST",
    "/api/auth/send-code",
    { email },
  );
  return res.data!;
}

// 验证码校验 + 绑定
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

**注意：** `request()` 方法中对 `code !== 200` 的错误处理需要调整，因为 send-code 接口在限频时返回 HTTP 200 但 `code=429`，verify-bind 验证失败时返回 HTTP 200 但 `code=400`。建议在这两个方法中单独处理非 200 code 的情况，将 message 作为用户提示返回，而不是抛出异常。

### 2. `src/tools/bind-identity.ts`

**改为两步交互流程：**

当前流程（单步）：
```
用户: "绑定邮箱 xxx@qq.com"
→ 调用 apiClient.bindEmail(email)
→ 返回 token，绑定完成
```

改后流程（两步）：
```
用户: "绑定邮箱 xxx@qq.com"
→ Step 1: 调用 apiClient.sendVerificationCode(email)
→ 返回提示: "验证码已发送到 xxx@qq.com，请查收邮箱并回复验证码"

用户: "验证码是 123456"
→ Step 2: 调用 apiClient.verifyAndBind(email, "123456")
→ 验证通过: 保存 token，绑定完成
→ 验证失败: 返回 "验证码错误，请重新输入或重新获取"
```

**具体改动：**

#### 方案 A：拆分为两个 Tool（推荐）

新增一个 `verify_email_code` 工具，与 `bind_identity` 配合：

- **`bind_identity`**: 接收 email → 调用 send-code → 返回"请回复验证码"
- **`verify_email_code`**: 接收 email + code → 调用 verify-bind → 保存 token

```typescript
// 新增 verify-email-code.ts
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

#### 方案 B：单 Tool + 可选参数

在现有 `bind_identity` 中增加可选的 `code` 参数：

- 无 code → 发送验证码（Step 1）
- 有 code → 验证绑定（Step 2）

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

如果采用方案 A（拆分两个 Tool），需要在 `index.ts` 中注册新工具 `verify_email_code`。

### 4. `src/types/index.ts`

新增类型定义：

```typescript
export interface SendCodeRequest {
  email: string;
}

export interface VerifyBindRequest {
  email: string;
  code: string;
}
```

## 不需要修改的文件

- `src/utils/storage.ts` — 存储逻辑不变
- `src/utils/polling-manager.ts` — 轮询逻辑不变
- `src/tools/initiate-meeting.ts` — 不涉及
- `src/tools/check-and-respond-tasks.ts` — 不涉及
- `src/tools/list-meetings.ts` — 不涉及

## 交互示例

### 正常流程
```
User: 帮我绑定邮箱 test@example.com
Bot:  验证码已发送到 test@example.com，请查收邮箱并回复 6 位数字验证码。

User: 验证码是 385721
Bot:  邮箱绑定成功！已自动登录，可以开始使用会议功能了。
```

### 验证码错误
```
User: 验证码是 000000
Bot:  验证码错误，请检查后重新输入。如需重新获取，请说"重新发送验证码"。
```

### 限频
```
User: 重新发送验证码
Bot:  请 45 秒后再试。
```
