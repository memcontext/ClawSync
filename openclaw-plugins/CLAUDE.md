# openclaw-plugins

OpenClaw 插件集合仓库，当前仅包含 **clawmeeting** 插件。

## 项目概览

ClawMeeting 是一个 OpenClaw 插件，将 AI Agent 变成智能会议协商助理。用户通过自然语言描述会议需求，插件自动与中央协调服务器交互，完成多方会议时间协商。

### 系统架构

```
用户 ↔ OpenClaw Agent ↔ ClawMeeting Plugin
                              ↓ HTTP 轮询 (10s)
                        API Server (FastAPI)
                        ↓
                        Agent Service (LangChain + 豆包 LLM)
```

本仓库只包含插件端代码，API Server 和 Agent Service 在 `../api-server/` 和 `../agent/` 中。

## 目录结构

```
openclaw-plugins/
└── clawmeeting/
    ├── index.ts                    # 插件入口（唯一导出 register 函数）
    ├── openclaw.plugin.json        # 插件清单
    ├── package.json
    ├── README.md / GUIDE.md
    ├── skills/
    │   └── clawmeeting-guide/SKILL.md
    └── src/
        ├── tools/
        │   ├── bind-identity.ts        # 邮箱绑定
        │   ├── initiate-meeting.ts     # 发起会议
        │   ├── check-and-respond-tasks.ts  # 查询任务 + 提交响应
        │   └── list-meetings.ts        # 查看会议列表/详情
        ├── types/index.ts              # TypeScript 类型定义
        └── utils/
            ├── api-client.ts           # HTTP 客户端
            ├── polling-manager.ts      # 后台轮询管理
            ├── mock-calendar.ts        # MVP 模拟日历数据
            └── storage.ts              # 本地持久化（~/.openclaw/clawmeeting/）
```

## 当前阶段

**MVP 阶段** — 核心协商流程已通，正在修 bug 和完善通知投递机制。mock-calendar 仍为模拟数据，未接入真实日历。

## 关键约束（必须遵守）

**本插件基于 OpenClaw 的插件适配方式开发，所有与框架交互的代码必须严格使用 OpenClaw SDK 已有的 API。禁止编造不存在的字段、方法或实现方式。**

### OpenClaw 插件 API（已确认可用）

插件入口为 `export default function register(api: any)`，`api` 对象提供以下接口：

#### 配置读取
- `api.config.plugins.entries[pluginId].config` — 读取插件配置
- `api.config.gateway.port` — Gateway 端口（默认 18789）
- `api.config.gateway.auth.token` — Gateway 认证 Token

#### 工具注册
```typescript
api.registerTool({
  name: string,
  description: string,
  inputSchema: object,        // JSON Schema
  async execute(id: string, params: any) {
    return { content: [{ type: "text", text: string }] };
  }
});
```

#### 服务注册
```typescript
api.registerService?.({
  id: string,
  start: (ctx: any) => void,
  stop: (ctx: any) => void,
});
```

#### 生命周期事件
```typescript
api.on?.("gateway_start", () => { ... });
api.on?.("gateway_stop", () => { ... });
api.on?.("before_prompt_build", (event, ctx) => {
  // ctx: { sessionKey, channelId, agentId, originatingTo, to, peerId, ... }
  // 返回值:
  return {
    appendSystemContext?: string,   // 追加到 system prompt
    prependContext?: string,        // 插入到用户消息前
  };
}, { priority: number });
```

#### CLI 注册
```typescript
api.registerCli?.((cliCtx: { program: any }) => {
  program.command("name").description("...").action(() => { ... });
}, { commands: ["name"] });
```

#### Gateway HTTP API（通过 fetch 调用，非 SDK 方法）
- `POST http://127.0.0.1:{port}/tools/invoke` — 调用 Gateway 工具
  - `tool: "message"` + `args: { action, channel, target, message }` — 直接发消息到渠道
  - `tool: "sessions_send"` + `args: { sessionKey, message, delivery: { mode } }` — 触发 Agent 回合

**以上是代码中实际使用的全部 API。如果需要新功能，先查阅 OpenClaw 文档确认 API 存在，不要假设或猜测。**

### 禁止事项
- 不要编造 `api` 上不存在的方法（如 `api.sendMessage`、`api.getSession` 等）
- 不要假设 `before_prompt_build` 的返回值支持未列出的字段
- 不要假设 `ctx` 上有未列出的属性
- 不要改变工具 `execute` 的返回格式（必须是 `{ content: [{ type: "text", text }] }`）
- 不要引入 OpenClaw SDK 未暴露的内部模块

## 技术栈

- **语言**: TypeScript (ES modules)
- **运行时**: Node.js
- **零运行时依赖** — 不依赖任何 npm 包，仅使用 Node.js 内置 API 和 OpenClaw SDK
- **通信**: HTTP 轮询（`fetch` + `setInterval`）
- **存储**: JSON 文件持久化到 `~/.openclaw/clawmeeting/`

## 核心机制

### 通知去重（三层）
1. **notifiedMeetings**（磁盘持久化）— CONFIRMED/OVER 等终态会议，通知一次后不再重复
2. **submittedMeetings**（内存）— 已提交 INITIAL_SUBMIT 的会议，防止轮询重复推送
3. **pendingDecisions**（磁盘持久化）— 等待用户决策的会议（COUNTER_PROPOSAL/FAILED），决策前不重复通知

### 通知投递（双通道 + 兜底）
- **message tool**（直接发）— Telegram/Discord/Feishu 等正式渠道，不经过 LLM
- **sessions_send**（触发 Agent）— webchat 或 fallback，触发 Agent 回合处理
- **pendingNotifications**（兜底）— 推送失败时暂存，下次 `before_prompt_build` 注入 prependContext

### Session 管理
- 在 `before_prompt_build` 中捕获主 session（排除 cron/run/subagent 临时 session）
- 持久化到 `session.json`，重启后恢复
- Telegram 渠道单独保存 ctx，支持叠加推送

### 会议状态机
```
COLLECTING → ANALYZING → CONFIRMED
                      → NEGOTIATING → CONFIRMED / FAILED
```

## 本地存储文件

位于 `~/.openclaw/clawmeeting/`：

| 文件 | 内容 |
|------|------|
| `credentials.json` | email, token, user_id |
| `session.json` | sessionKey, channel, lastTo |
| `notified-meetings.json` | 已通知的会议 ID 列表 |
| `pending-decisions.json` | 等待用户决策的会议 ID 列表 |
| `telegram-ctx.json` | Telegram 渠道 session |
| `preferences.json` | 用户偏好（预留） |

## 开发注意事项

- 修改工具的 `inputSchema` 时，必须同步更新对应的类型定义（`src/types/index.ts`）
- API 端点必须与服务端 API v1.0.0 严格对齐（见 `src/utils/api-client.ts`）
- 插件入口使用单例守卫（`_registered`），防止框架多次调用 register
- `before_prompt_build` 中非主 session 直接返回 `{}`，节省 token
- 后台轮询为纯 HTTP，不消耗 LLM token
- 所有 `api.xxx?.()` 使用可选链调用，兼容不同版本的 OpenClaw SDK

## 开发环境配置

- **测试服务端地址**: `http://192.168.22.28:8000`（开发阶段）
- **符号链接**: `C:\Users\jushi\.openclaw\extensions\clawmeeting` → `D:\lll\pl\openclaw-plugins\clawmeeting`
- **开发工作流**: 改代码 → 重启网关 → 直接测试（无需手动迁移）
- **openclaw.json 配置**: 已加 `plugins.allow: ["clawmeeting"]` 和 `plugins.load.paths`
- **测试账号**: `2226957164@qq.com`（user_id: 1）

## 当前开发进度

### 已完成

1. **邮箱绑定改造为两步验证码流程**（方案 A：拆分两个 Tool）
   - `bind_identity` → `POST /api/auth/send-code`（发送验证码）
   - `verify_email_code`（新增） → `POST /api/auth/verify-bind`（校验验证码完成绑定）
   - 旧接口 `POST /api/auth/bind` 保留但标记 Deprecated
   - 变更文件: `api-client.ts`, `types/index.ts`, `bind-identity.ts`, `verify-email-code.ts`(新建), `index.ts`
   - curl 接口测试已通过

2. **合入远程仓库必要改动**
   - 时间格式约束 `00:00-23:59, never use 24:00`（`check-and-respond-tasks.ts`, `initiate-meeting.ts`）
   - `notifiedMeetings` 上限 200 条，超过时清除最早一半（`index.ts`）

3. **未采用的远程改动**（有问题，暂不合入）
   - Telegram 推送重构（从 sessionKey 解析 channel）— 依赖 sessionKey 格式约定不稳健，丢失叠加推送能力，dm 私聊被排除

### 待测试

- 通过 OpenClaw Agent 实际调用 `bind_identity` + `verify_email_code` 完成邮箱绑定全流程
