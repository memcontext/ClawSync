# ClawMeeting — AI Meeting Negotiation Plugin for OpenClaw

An OpenClaw plugin that turns your AI agent into a meeting scheduling assistant. It connects to a central coordination server, automatically collects availability from all participants, and negotiates the best meeting time — all through natural conversation.

## How It Works

```
You ←→ AI Agent ←→ ClawMeeting Plugin ←→ Coordination Server ←→ Other Participants' Agents
```

1. **You say** "Schedule a meeting with bob@example.com tomorrow afternoon"
2. **Your agent** parses the request and calls the plugin
3. **The plugin** sends it to the coordination server
4. **The server** notifies all invitees' plugins to collect their availability
5. **An AI coordinator** on the server finds the best time (or negotiates if there's a conflict)
6. **You get notified** when the meeting is confirmed

The whole process runs in the background. You only need to intervene when there's a conflict that requires your decision.

## Features

- **Natural language scheduling** — "Book a 30-min standup with Alice and Bob tomorrow 2-5pm"
- **Automatic invitation handling** — When someone invites you, your agent checks your calendar and memory, then submits availability automatically
- **Multi-round negotiation** — If times conflict, the server's AI coordinator proposes compromises; your agent presents them to you for approval
- **Privacy-first** — Only your available time slots are shared; the server never sees what you're busy with
- **Background polling** — Checks for new tasks every 10 seconds (pure HTTP, zero LLM token cost)
- **Deduplication** — Three-layer dedup system prevents duplicate notifications across restarts

## Install

```bash
openclaw plugins install clawmeeting
```

Or add to your config manually:

```json5
{
  plugins: {
    entries: {
      clawmeeting: {
        enabled: true,
        config: {
          serverUrl: "http://39.105.143.2:7010",  // coordination server
          pollingIntervalMs: 10000,                // poll every 10s
          autoRespond: true                        // enable background polling
        }
      }
    }
  }
}
```

Then restart: `openclaw gateway restart`

## Quick Start

### 1. Bind your email

```
You: Bind my email alice@company.com
```

This registers your identity with the coordination server. Only needed once — credentials persist across restarts.

### 2. Schedule a meeting

```
You: Schedule a 1-hour project review with bob@company.com and charlie@company.com tomorrow 2-5pm
```

The agent parses title, duration, invitees, and your available slots, then sends the request.

### 3. That's it

The plugin handles the rest in the background:
- ✅ Time match found → you get a confirmation with meeting details
- 🔄 Conflict detected → the coordinator proposes a compromise, your agent asks you to accept/reject/counter
- ❌ Negotiation failed → your agent asks if you want to cancel or try different times

## Tools (4)

These are the functions your AI agent can call:

| Tool | Purpose | Server API |
|------|---------|------------|
| `bind_identity` | Register email, get auth token | POST /api/auth/bind |
| `initiate_meeting` | Start a new meeting negotiation | POST /api/meetings |
| `check_and_respond_tasks` | View pending tasks / submit responses | GET /api/tasks/pending, POST /api/meetings/{id}/submit |
| `list_meetings` | View meeting list or details | GET /api/meetings, GET /api/meetings/{id} |

### `check_and_respond_tasks` response types

| Type | When | Needs slots? |
|------|------|-------------|
| `INITIAL` | First-time availability submission | Yes |
| `NEW_PROPOSAL` | Counter-propose during negotiation | Yes |
| `ACCEPT_PROPOSAL` | Accept the coordinator's suggestion | No |
| `REJECT` | Decline participation | No |

## Architecture

```
┌────────────────────── Gateway Process (Node.js) ──────────────────────┐
│                                                                       │
│  ┌─────────────┐         ┌──────────────────────────────────┐        │
│  │  Framework   │         │        ClawMeeting Plugin         │        │
│  │             │         │                                  │        │
│  │  Agent      │◄────────│  4 Tools (LLM-callable)          │        │
│  │  Session    │         │  PollingManager (every 10s)       │        │
│  │  Heartbeat  │◄────────│  pushMessage (system event)       │        │
│  │  Prompt     │◄────────│  before_prompt_build hook         │        │
│  └─────────────┘         └──────────┬───────────────────────┘        │
│                                     │ HTTP                           │
└─────────────────────────────────────┼────────────────────────────────┘
                                      ▼
                         ┌──────────────────────────┐
                         │  Coordination Server      │
                         │  • Meeting lifecycle      │
                         │  • AI time coordination   │
                         │  • Cross-user messaging   │
                         └──────────────────────────┘
```

### Notification flow (plugin → agent → user)

The plugin uses OpenClaw's **system event** mechanism to deliver notifications:

```
Polling discovers new task
  → enqueueSystemEvent(text, { sessionKey })   // push text into in-memory queue
  → requestHeartbeatNow()                      // immediately trigger a heartbeat agent turn
  → Framework drains queue into system prompt   // "System: [12:00] [ClawMeeting ...] ..."
  → Agent sees notification → replies to user via normal channel delivery
```

This avoids the agent-to-agent flow entirely — no announce step, no message leakage.

### Deduplication (3 layers)

| Set | Purpose | Persisted? |
|-----|---------|-----------|
| `notifiedMeetings` | CONFIRMED/OVER — notify once | ✅ Disk |
| `submittedMeetings` | INITIAL_SUBMIT — don't re-submit | ❌ Memory only |
| `pendingDecisions` | COUNTER_PROPOSAL/FAILED — don't re-notify while waiting | ✅ Disk |

### Local storage

```
~/.openclaw/clawmeeting/
  ├── credentials.json        ← { email, token, user_id }
  ├── session.json            ← { sessionKey, channel }
  ├── notified-meetings.json  ← ["mtg_xxx", ...]
  └── pending-decisions.json  ← ["mtg_yyy", ...]
```

## Meeting Lifecycle

```
COLLECTING → ANALYZING → CONFIRMED
                       → NEGOTIATING → CONFIRMED / FAILED
```

| Status | Meaning |
|--------|---------|
| COLLECTING | Waiting for all participants to submit availability |
| ANALYZING | AI coordinator is finding the best time |
| CONFIRMED | Meeting time finalized |
| NEGOTIATING | Time conflict — coordinator sent compromise proposals |
| FAILED | Could not find a mutually agreeable time |

## Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `serverUrl` | string | `http://39.105.143.2:7010` | Coordination server URL |
| `pollingIntervalMs` | number | `10000` | Background polling interval (ms) |
| `autoRespond` | boolean | `true` | Enable background polling and auto-notification |

## CLI

```bash
openclaw clawmeeting-status
```

Shows server URL, polling status, bound email, and notification stats.

## File Structure

```
clawmeeting/
├── index.ts                    # Plugin entry: register, polling, hooks, tools
├── package.json
├── openclaw.plugin.json        # Plugin manifest
├── README.md                   # This file
├── GUIDE.md                    # End-user guide
├── skills/
│   └── clawmeeting-guide/
│       └── SKILL.md            # Agent skill for onboarding
└── src/
    ├── tools/
    │   ├── bind-identity.ts            # Tool: email binding
    │   ├── initiate-meeting.ts         # Tool: create meeting
    │   ├── check-and-respond-tasks.ts  # Tool: poll tasks + submit responses
    │   └── list-meetings.ts            # Tool: view meetings
    ├── types/
    │   └── index.ts                    # TypeScript type definitions
    └── utils/
        ├── api-client.ts               # HTTP client for coordination server
        ├── polling-manager.ts           # Background polling with concurrency guard
        ├── mock-calendar.ts             # Mock calendar data (legacy)
        └── storage.ts                   # Local persistence (credentials, session, dedup)
```

## License

MIT

---

# ClawMeeting — OpenClaw 智能会议协商插件

一个 OpenClaw 插件，把你的 AI Agent 变成会议调度助手。它连接中央协调服务端，自动收集所有参会者的空闲时间，通过 AI 协调找到最佳会议时间——全程自然语言对话。

## 工作原理

```
你 ←→ AI Agent ←→ ClawMeeting 插件 ←→ 协调服务端 ←→ 其他参会者的 Agent
```

1. **你说** "帮我约 bob@example.com 明天下午开会"
2. **你的 Agent** 解析请求，调用插件
3. **插件** 发送到协调服务端
4. **服务端** 通知所有被邀请人的插件收集空闲时间
5. **服务端 AI 协调员** 找到最佳时间（如果有冲突就发起协商）
6. **你收到通知** 会议已确认

整个过程在后台运行。只有出现冲突需要你拍板时才会打扰你。

## 功能特性

- **自然语言约会议** — "约 Alice 和 Bob 明天下午 2-5 点开个 30 分钟的站会"
- **自动处理邀请** — 别人约你时，Agent 自动查日历和记忆，帮你回复空闲时间
- **多轮协商** — 时间冲突时，服务端 AI 协调员提出妥协方案，Agent 转达给你选择
- **隐私优先** — 只共享你的可用时间段，服务端看不到你在忙什么
- **后台轮询** — 每 10 秒检查新任务（纯 HTTP，不消耗 LLM Token）
- **三层去重** — 跨重启的通知去重，不会反复推送同一条消息

## 安装

```bash
openclaw plugins install clawmeeting
```

或手动添加到配置：

```json5
{
  plugins: {
    entries: {
      clawmeeting: {
        enabled: true,
        config: {
          serverUrl: "http://39.105.143.2:7010",  // 协调服务端地址
          pollingIntervalMs: 10000,                // 每 10 秒轮询
          autoRespond: true                        // 启用后台轮询
        }
      }
    }
  }
}
```

重启生效：`openclaw gateway restart`

## 快速开始

### 1. 绑定邮箱

```
你：帮我绑定邮箱 alice@company.com
```

在协调系统中注册你的身份。只需一次，凭证持久化存储。

### 2. 发起会议

```
你：帮我约 bob@company.com 和 charlie@company.com 明天下午开一小时的项目讨论会
```

Agent 自动解析标题、时长、被邀请人和你的空闲时间，发送请求。

### 3. 等结果

插件在后台处理一切：
- ✅ 时间匹配 → 收到确认通知，含完整会议信息
- 🔄 时间冲突 → 协调员提出妥协方案，Agent 问你接受/拒绝/反提议
- ❌ 协商失败 → Agent 问你要取消还是换个时间重试

## 工具（4 个）

AI Agent 可以调用的函数：

| 工具 | 用途 | 服务端 API |
|------|------|-----------|
| `bind_identity` | 注册邮箱，获取认证 Token | POST /api/auth/bind |
| `initiate_meeting` | 发起新会议协商 | POST /api/meetings |
| `check_and_respond_tasks` | 查看待办 / 提交响应 | GET /api/tasks/pending, POST /api/meetings/{id}/submit |
| `list_meetings` | 查看会议列表或详情 | GET /api/meetings, GET /api/meetings/{id} |

### `check_and_respond_tasks` 响应类型

| 类型 | 场景 | 需要时间段？ |
|------|------|------------|
| `INITIAL` | 首次提交空闲时间 | 是 |
| `NEW_PROPOSAL` | 协商中提出新时间 | 是 |
| `ACCEPT_PROPOSAL` | 接受协调方建议 | 否 |
| `REJECT` | 拒绝参加 | 否 |

## 架构

```
┌────────────────────── Gateway 进程 (Node.js) ─────────────────────────┐
│                                                                       │
│  ┌─────────────┐         ┌──────────────────────────────────┐        │
│  │  框架核心     │         │        ClawMeeting 插件           │        │
│  │             │         │                                  │        │
│  │  Agent      │◄────────│  4 个 Tools（LLM 可调用）          │        │
│  │  Session    │         │  轮询管理器（每 10 秒）             │        │
│  │  Heartbeat  │◄────────│  pushMessage（system event）      │        │
│  │  Prompt 构建 │◄────────│  before_prompt_build 钩子        │        │
│  └─────────────┘         └──────────┬───────────────────────┘        │
│                                     │ HTTP                           │
└─────────────────────────────────────┼────────────────────────────────┘
                                      ▼
                         ┌──────────────────────────┐
                         │  协调服务端                │
                         │  • 会议生命周期管理        │
                         │  • AI 时间协调            │
                         │  • 跨用户消息中转          │
                         └──────────────────────────┘
```

### 通知流程（插件 → Agent → 用户）

插件使用 OpenClaw 的 **system event** 机制投递通知：

```
轮询发现新任务
  → enqueueSystemEvent(text, { sessionKey })   // 文本放入内存队列
  → requestHeartbeatNow()                      // 立即触发 heartbeat agent turn
  → 框架 drain 队列拼入 system prompt           // "System: [12:00] [ClawMeeting ...] ..."
  → Agent 看到通知 → 通过常规 channel delivery 回复用户
```

完全绕开 agent-to-agent 流程——没有 announce step，没有消息泄露。

### 去重系统（3 层）

| 集合 | 用途 | 持久化？ |
|------|------|---------|
| `notifiedMeetings` | CONFIRMED/OVER 纯通知，只通知一次 | ✅ 磁盘 |
| `submittedMeetings` | INITIAL_SUBMIT 已提交，不重复提交 | ❌ 仅内存 |
| `pendingDecisions` | COUNTER_PROPOSAL/FAILED 等待用户决策期间不重复通知 | ✅ 磁盘 |

### 本地存储

```
~/.openclaw/clawmeeting/
  ├── credentials.json        ← { email, token, user_id }
  ├── session.json            ← { sessionKey, channel }
  ├── notified-meetings.json  ← ["mtg_xxx", ...]
  └── pending-decisions.json  ← ["mtg_yyy", ...]
```

## 会议状态机

```
COLLECTING → ANALYZING → CONFIRMED
                       → NEGOTIATING → CONFIRMED / FAILED
```

| 状态 | 含义 |
|------|------|
| COLLECTING | 等待所有参与者提交空闲时间 |
| ANALYZING | AI 协调员正在寻找最佳时间 |
| CONFIRMED | 会议时间已确定 |
| NEGOTIATING | 时间冲突——协调员已发送妥协方案 |
| FAILED | 无法找到大家都满意的时间 |

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `serverUrl` | string | `http://39.105.143.2:7010` | 协调服务端地址 |
| `pollingIntervalMs` | number | `10000` | 后台轮询间隔（毫秒） |
| `autoRespond` | boolean | `true` | 是否启用后台轮询和自动通知 |

## CLI 命令

```bash
openclaw clawmeeting-status
```

显示服务端地址、轮询状态、绑定邮箱和通知统计。

## 文件结构

```
clawmeeting/
├── index.ts                    # 插件入口：注册、轮询、钩子、工具
├── package.json
├── openclaw.plugin.json        # 插件清单
├── README.md                   # 本文件
├── GUIDE.md                    # 终端用户引导文档
├── skills/
│   └── clawmeeting-guide/
│       └── SKILL.md            # Agent 引导技能
└── src/
    ├── tools/
    │   ├── bind-identity.ts            # 工具：邮箱绑定
    │   ├── initiate-meeting.ts         # 工具：发起会议
    │   ├── check-and-respond-tasks.ts  # 工具：轮询任务 + 提交响应
    │   └── list-meetings.ts            # 工具：查看会议
    ├── types/
    │   └── index.ts                    # TypeScript 类型定义
    └── utils/
        ├── api-client.ts               # 协调服务端 HTTP 客户端
        ├── polling-manager.ts          # 后台轮询（含并发守卫）
        ├── mock-calendar.ts            # 模拟日历数据（遗留）
        └── storage.ts                  # 本地持久化（凭证、会话、去重）
```

## 开源协议

MIT
