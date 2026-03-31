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
        │   ├── bind-identity.ts        # 邮箱绑定 Step1（发送验证码）
        │   ├── verify-email-code.ts    # 邮箱绑定 Step2（校验验证码完成绑定）
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

**MVP 阶段** — 核心协商流程已通，邮箱验证码绑定已完成，正在解决通知投递 UX 问题。mock-calendar 仍为模拟数据，未接入真实日历。

## 编程原则（必须遵守）

### 通用化优先

实现功能时必须优先考虑通用方案，而非为特定场景硬编码：

- **数据结构通用化**：用 `Map<string, T>` / 配置驱动代替硬编码。例如渠道推送不要写 `telegramCtx`，要写 `extraChannels: Map<string, SessionContext>` 支持所有渠道。
- **自动发现优先**：能从配置/环境自动检测的就不要让用户手动操作。例如从 `api.config.channels` 遍历已启用渠道 + 读取 pairing allow store 自动发现推送目标，而非要求用户先发消息触发捕获。
- **渐进增强**：基础功能（主 session 推送）永远可用，增强功能（额外渠道推送）按条件叠加 — 有就推、没有就跳过，不影响主流程。
- **新增渠道零改动**：如果用户将来配了 Discord/Feishu，插件应自动发现并推送，不需要改代码。

### 推送分流原则

不同类型的通知有不同的投递策略，不要一刀切：

| 类型 | 是否推送给用户 | 原因 |
|------|--------------|------|
| INITIAL_SUBMIT | ❌ 静默 Agent 处理 | 用户不需要知道，Agent 自动查日历提交 |
| COUNTER_PROPOSAL | ✅ 推送 | 需要用户决策 |
| MEETING_FAILED | ✅ 推送 | 需要用户决策（取消/重试） |
| MEETING_CONFIRMED | ✅ 推送 | 告知用户结果 |
| MEETING_OVER | ✅ 推送 | 告知用户结果 |

### 框架交互约束

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
  - `tool: "sessions_send"` + `args: { sessionKey, role, message, delivery: { mode }, announce }` — 触发 Agent 回合（role: "system" 避免用户气泡，announce: false 防止广播）

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

### 通知投递（任务队列：collectTasks → processQueue）
轮询发现新任务 → `collectTasks` 去重+入队（毫秒级） → `processQueue`（5s 定时器）逐条处理：
1. **sessions_send** 到主 session（`agent:main:main`，60s 超时）→ 触发 agent turn
2. **提取 reply** — 从 response 中提取 `result.details.reply`
3. **message tool 分发** — 将 reply 推送到所有额外渠道（Telegram/飞书/Discord）
4. **失败重试** — sessions_send 失败则留在队列，下轮重试（最多 3 次）
5. **超时放弃** — 3 次失败后用 `buildDirectNotification`（用户友好格式）fallback 到 prependContext + message tool
6. **Agent Offline** — 入队超 10 分钟未处理 → 自动 `REJECT` + 通知用户

**message tool** 仅在正式渠道可用，webchat 不支持。webchat 用户通过 sessions_send 触发的 agent turn 直接看到回复。

### 推送渠道解析
- `parseChannelTarget(sk)` 从任意 sessionKey 解析 channel 和 target
- sessionKey 格式: `"agent:<agentId>:<channel>:<kind>:<id>"`，kind 支持 group/channel/dm/direct
- webchat/main sessionKey 返回 null（不支持 message tool）
- message tool 参数: `{ tool: "message", args: { action: "send", channel, target, message } }`

### 多渠道自动发现
- **启动时**：遍历 `api.config.channels`，找 `enabled: true` 的渠道，读 `~/.openclaw/credentials/{channel}-default-allowFrom.json` 自动发现推送目标
- **运行时**：`before_prompt_build` 捕获所有非 webchat 渠道的 session，持续更新
- **存储**：每个渠道独立持久化到 `channel-{name}.json`，重启后恢复

### Session 管理
- 在 `before_prompt_build` 中捕获主 session（排除 cron/run/subagent 临时 session）
- 持久化到 `session.json`，重启后恢复
- 额外渠道 session 单独追踪到 `extraChannels: Map<string, SessionContext>`

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
| `session.json` | 主 session（sessionKey, channel） |
| `notified-meetings.json` | 已通知的会议 ID 列表 |
| `pending-decisions.json` | 等待用户决策的会议 ID 列表 |
| `channel-{name}.json` | 各渠道 session（如 `channel-telegram.json`） |
| `preferences.json` | 用户偏好（预留） |

## 开发注意事项

- 工具 schema 字段名必须用 **`parameters`**（与飞书等官方插件一致），由 `api.registerTool()` 注册
- 修改工具的 `inputSchema` 时，必须同步更新对应的类型定义（`src/types/index.ts`）
- API 端点必须与服务端 API v1.0.0 严格对齐（见 `src/utils/api-client.ts`）
- 插件入口拆分为"工具注册"（每次 register() 都执行）和"运行时初始化"（`_shared.initialized` 守卫，只执行一次）。OpenClaw 会为不同 Registry 多次调用 register()，工具必须每次都注册到新 registry，运行时单例通过模块级 `_shared` 对象共享
- `before_prompt_build` 中非主 session 直接返回 `{}`，节省 token
- 后台轮询为纯 HTTP，不消耗 LLM token
- 所有 `api.xxx?.()` 使用可选链调用，兼容不同版本的 OpenClaw SDK

## 开发环境配置

- **测试服务端地址**: `http://39.105.143.2:7010`（开发阶段）
- **插件加载**: `plugins.load.paths: ["D:\\lll\\pl\\openclaw-plugins\\clawmeeting"]`（直接加载本地代码）
- **开发工作流**: 改代码 → 重启网关 → 直接测试
- **openclaw.json 配置**: `plugins.allow: ["clawmeeting"]` + `plugins.load.paths`
- **测试账号**:

| 邮箱 | Token | User ID |
|------|-------|---------|
| `uppxxcco@gmail.com` | `sk-E4AmW5PU...` | 1 |
| `runfengsun@gmail.com` | `sk-suVLlfRb...` | 3 |
| `2226957164@qq.com` | `sk-mSOMouL1...` | 6 |

## 当前开发进度

### 已完成

1. **邮箱绑定两步验证码流程** — `bind_identity`(发送验证码) + `verify_email_code`(校验+绑定)
2. **推送架构重构** — sessions_send → 提取 reply → message tool 分发到所有渠道
3. **多渠道泛化** — `extraChannels: Map<string, SessionContext>`，新增渠道零改动
4. **sessions_send response 解析** — 检测 forbidden 状态 + 提取 agent reply
5. **去重防护** — 三层去重 + submittedMeetings 不回滚（防超时导致重复）+ 主 session 脏数据校验
6. **Debug 日志** — 统一 `[CM:模块]` 前缀，覆盖 API/轮询/推送/工具/钩子/存储
7. **任务队列重构** — `collectTasks`(入队) + `processQueue`(逐条处理)，替代旧的 batch `autoRespondToTasks`
   - 轮询回调只做入队（毫秒级，不阻塞）
   - 独立 5s 定时器逐条处理：sessions_send → 提取 reply → message tool 分发
   - 失败重试最多 3 次，fallback 用 `buildDirectNotification`（用户友好格式，不再是原始 agent 指令）
   - Agent Offline：入队超 10 分钟 → 自动 REJECT + `"Agent offline"` + 通知用户
   - sessions_send 超时 30s → 60s
8. **preference_note 必填** — agent 提交时必须带用户偏好/约束说明
9. **FAILED 重试指令增强** — 明确列出所有可修改参数（时间/时长/参与者）
10. **GUIDE.md 合并到 SKILL.md** — 去掉重复文档，统一为一个 agent skill 文件

### 待优化

- webchat 推送 UX（sessions_send 的系统消息气泡仍可见，等 OpenClaw 开放 `chat.inject` 给插件）
- 飞书渠道实测（代码已支持，待配置飞书 Bot 验证）
- npm 发布新版（当前 npm 为 1.0.18，本地代码已超前）

---

## 2025-03-25 探索记录：消息注入 UX 问题

### 问题描述

插件通过 `sessions_send` 向 webchat 推送通知，导致两个严重 UX 问题：
1. **注入消息以用户气泡形式显示** — 用户在 webchat 看到自己没发过的消息（`sessions_send` 以 `role: "user"` 写入 transcript）
2. **框架 ping-pong + announce 产生额外消息** — "ANNOUNCE_SKIP"、"NO-reply-from-agent" 等工件（已通过 `delivery.mode=none` + `maxPingPongTurns=0` 消除）

目前第 2 个问题已解决，第 1 个问题（用户气泡）是 `sessions_send` 的固有限制，需要找替代方案。

### 已探索并排除的方案

| 方案 | 结论 | 原因 |
|------|------|------|
| `chat.inject` via `/tools/invoke` | 不可行 | `chat.inject` 是 Gateway WS 方法，不是 tool；`/tools/invoke` 只路由 `createOpenClawTools()` 注册的工具 |
| `chat.inject` via WebSocket | 不可行 | 需要 `operator.admin` scope；插件 token auth 无 device identity，gateway 的 `clearUnboundScopes()` 会清空所有 scope；device identity 需要加密密钥对配对流程，插件 SDK 不提供 |
| `sessions_spawn` via `/tools/invoke` | 不可行 | 从插件后台调用时无"请求方 session"，announce step 无投递目标 |
| `api.runtime.subagent.run()` | 部分可行 | 可运行任务（`deliver: false`），但无法主动推结果到 webchat |
| 直接写 transcript 文件 | 部分可行 | 可写文件，但 webchat 不会实时刷新（无 broadcast 事件） |
| `enqueueSystemEvent` + `requestHeartbeatNow` | **待定** | 函数存在且可调用，但实测 heartbeat runner 未触发（疑似模块实例隔离问题，见下文） |

### chat.inject 不可用的根本原因

`chat.inject` 是 OpenClaw 官方 Gateway WS 方法，功能完美（以 assistant 角色写入 transcript + 实时广播 + 不触发 agent turn），但安全模型阻止插件调用：

1. 它是 WS 方法 → `/tools/invoke` 无法路由
2. WS 调用需要 `operator.admin` scope → 实测返回 `missing scope: operator.admin`
3. 获得 admin scope 需要 device identity（加密密钥对 + 配对流程）
4. 插件 SDK 不提供 device identity API — 这是为前端客户端设计的

### enqueueSystemEvent + requestHeartbeatNow 探索

这是 OpenClaw 内部 cron jobs / notification events 向 main session 推送通知的官方机制：

```typescript
// 插件 SDK runtime 原生提供
api.runtime.system.enqueueSystemEvent(notificationText, {
  sessionKey: "agent:main:main",
  contextKey: "clawmeeting:meeting-xxx"  // 自动去重
});
api.runtime.system.requestHeartbeatNow({ reason: "clawmeeting-notification" });
```

**实测结果**：
- `api.runtime.system` 存在，两个函数类型均为 `function`
- `enqueueSystemEvent` 调用成功无报错
- `requestHeartbeatNow` 调用成功无报错
- **但 heartbeat runner 从未实际执行**，webchat 无任何变化

**疑似原因**：模块实例隔离 — 插件拿到的 `requestHeartbeatNow` 可能操作的是不同模块实例的 `pendingWakes` Map，与 gateway 主进程的 heartbeat handler 不在同一内存空间。需要进一步验证。

**相关源码位置**：
- `pi-embedded-CwMQzdKD.js:72005-72165` — heartbeat wake 系统（`pendingWakes` Map, `handler`, `schedule()`）
- `runtime-D60f3HDj.js:606-613` — `createRuntimeSystem()` 返回 `enqueueSystemEvent` 和 `requestHeartbeatNow`
- `gateway-cli-CJG95mu_.js:1697-1860` — `runHeartbeatOnce` 完整流程
- `system-events-B97dSsCm.js:35-77` — `enqueueSystemEvent` 实现
- `server-node-events-NTgh2HlT.js` — 官方使用范例（notification events 用同样的 enqueue + heartbeat 模式）

### 当前状态与后续方向

**当前方案**（已在用）：sessions_send → reply 提取 → message tool 分发
- 主 session: `sessions_send` + `role: "system"` + `announce: false` + `delivery.mode: "none"` → 从 response 提取 `reply`
- 额外渠道: `message tool`（`action: "send"`）推送 agent reply（或 fallback 到 `buildDirectNotification`）
- 启动时自动从 `api.config.channels` + pairing allow store 发现推送目标
- 运行时 `before_prompt_build` 持续捕获新渠道 session
- `sessions_send` 到非 webchat session → **forbidden**（`visibility=tree`），因此额外渠道只能用 message tool

**待确认**（需向 OpenClaw 官方咨询）：
- 插件 SDK 是否会新增 `api.runtime.chat.inject()` 或等价方法？
- `sessions_send` 已支持 `role: "system"` 参数（避免用户气泡），但仍非 assistant 角色
- `enqueueSystemEvent` + `requestHeartbeatNow` 在插件中不生效是 bug 还是设计如此？
- 是否有其他官方推荐的插件主动推送机制？

### 关键 OpenClaw 文档位置

| 文档 | 路径 |
|------|------|
| Session Tools (sessions_send 参数) | `docs/concepts/session-tool.md` |
| WebChat (chat.inject 行为) | `docs/web/webchat.md` |
| Gateway WS 协议 (帧格式) | `docs/concepts/typebox.md` |
| 插件 SDK runtime | `docs/plugins/sdk-runtime.md` |
| Gateway 工具配置 | `docs/gateway/tools-invoke-http-api.md` |
