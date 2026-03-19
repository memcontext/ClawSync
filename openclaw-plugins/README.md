# ClawSync v1.0.0 — 智能会议协商代理插件

OpenClaw 插件，作为用户的贴身会议助理，负责收集情报、保护隐私并代表用户进行初步博弈。
连接中央协调服务端，代表用户完成会议的发起、时间收集、多轮协商等全流程。

当前版本 (MVP) 定位：通信链路跑通，结构化请求/响应格式正确，使用 Mock 日历数据模拟用户时间和偏好。

## 架构

```
用户 ←→ OpenClaw Agent ←→ ClawSync 插件 (本项目)
                                │
                          HTTP Polling (每10秒，不消耗 Token)
                                │
                                ▼
                       中央协调端 (API Server)
                       ├── FastAPI 核心框架
                       ├── SQLite 持久化层 (users / meetings / negotiation_logs)
                       ├── 状态机引擎
                       └── Coordinator Agent (LLM 驱动)
```

插件通过 HTTP 与服务端通信，采用主动轮询 (Polling) 获取待办任务，不依赖 WebSocket，适配复杂内网环境。
Session 管理、消息推送等逻辑纯属插件端内部机制，不影响服务端 API 接口。

接口格式严格对齐 API_REFERENCE.md v1.0.0。

## 三个核心 Tools

| Tool 名称 | 对应 API | 功能 |
|-----------|---------|------|
| `bind_identity` | API 1: POST /api/auth/bind | 邮箱绑定/注册，Token 本地安全存储，自动启动后台轮询 |
| `initiate_meeting` | API 2: POST /api/meetings | 发起会议协商，自动附带发起人日历空闲时间与偏好 |
| `check_and_respond_tasks` | API 5+6: POST /api/meetings/{id}/submit + GET /api/tasks/pending | 拉取待办任务 → 所有任务交给 Agent 处理（读日历/问用户） → Agent 调本工具提交 |

## 安装

1. 将本目录放置到 `D:\lll\mt\clawsync`（或任意 OpenClaw 可发现的路径）
2. 配置 `~/.openclaw/openclaw.json`（Windows: `%USERPROFILE%\.openclaw\openclaw.json`）:

```json
{
  "plugins": {
    "entries": {
      "clawsync": {
        "enabled": true,
        "config": {
          "serverUrl": "http://localhost:8000",
          "pollingIntervalMs": 10000,
          "autoRespond": true
        }
      }
    },
    "load": {
      "paths": ["D:\\lll\\mt\\clawsync"]
    }
  }
}
```

3. 重启 Gateway: `openclaw gateway restart`
4. 验证: `openclaw plugins inspect clawsync`

### 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| serverUrl | string | http://localhost:8000 | 中央协调端地址 |
| pollingIntervalMs | number | 10000 | 后台轮询间隔（毫秒），默认 10 秒 |
| autoRespond | boolean | true | 是否启用后台轮询 |

## 轮询与任务处理机制

### 轮询生命周期

轮询完全由 Gateway 生命周期钩子管理：

- `after_agent_start` 钩子：Gateway 就绪后，有 Token 就启动轮询
- `before_agent_stop` 钩子：Gateway 关闭时清理定时器
- `bind_identity` 回调：首次绑定成功后启动轮询（覆盖 Gateway 启动时还没绑定的场景）

轮询本身是纯 HTTP 请求 (GET /api/tasks/pending)，**不经过 LLM，不消耗 Token**。

### 任务处理流程

所有任务类型都交给 Agent 处理，插件不自动提交：

```
setInterval (每10秒)
  → GET /api/tasks/pending (纯 HTTP)
  → 发现任务 → pushMessageToUser() 注入消息到用户 session
  → Agent (LLM) 被唤醒
  → Agent 调用 check_and_respond_tasks() 无参数 → 获取任务详情 + 日历数据
  → INITIAL_SUBMIT:   Agent 根据日历选时间 → 带参数调用提交
  → COUNTER_PROPOSAL: Agent 展示建议给用户 → 等用户决定 → 带参数调用提交
```

### 扩展预留

| 扩展点 | 用途 |
|--------|------|
| `PollingManager.onTaskReceived` | 后续接入容忍度/自动决策中间件 |
| `PollingManager.onNeedAgentAction` | 当前用于推送通知唤醒 Agent |
| `PollingManager.updateInterval()` | 服务端繁忙时动态退避 |
| `bind_identity` 的 `onBindSuccess` 回调 | 后续可触发更多初始化流程 |

## 生命周期钩子（共 3 个）

| 钩子 | 触发时机 | 做什么 |
|------|---------|--------|
| `before_agent_start` | Gateway 启动中 | 注入 system prompt（引导绑定 / 告知已就绪） |
| `after_agent_start` | Gateway 就绪后 | 有 Token 就启动轮询定时器 |
| `before_agent_stop` | Gateway 关闭前 | 清理轮询定时器 |

## Session 管理

插件内部维护 session 上下文，确保轮询推送的通知消息回到用户发起绑定时的同一个对话窗口。

- 用户调用 `bind_identity` 或 `initiate_meeting` 时，从 OpenClaw ctx 中捕获 sessionKey / channel / peerId
- 持久化到 `~/.openclaw/clawsync/session.json`，OpenClaw 重启后自动恢复
- 轮询推送时指定该 session，避免创建新对话窗口
- **纯插件端机制，不影响服务端 API 接口格式**

## 本地存储

存储目录：`~/.openclaw/clawsync/`

| 文件 | 内容 |
|------|------|
| credentials.json | email, token, user_id |
| session.json | sessionKey, channel, peerId |
| preferences.json | 用户长期偏好（后续由 Agent 沉淀） |

## MVP Mock 数据

当前版本使用 `mock-calendar.ts` 中的写死数据：

- 空闲时段：未来 3 天，每天 10:00-12:00 和 14:00-17:00
- 繁忙时段：当天 09:00-10:00, 12:00-13:30
- 用户偏好：不喜欢早会、周五下午不开会、连续会议需 15 分钟缓冲
- API 2 使用字符串格式 `"2026-03-18 14:00-17:00"`
- API 5 使用对象格式 `{ start: "2026-03-18 14:00", end: "2026-03-18 17:00" }`

后续 Phase 3 将替换为真实日历集成。

## CLI 命令

```bash
openclaw clawsync-status
```

输出示例：
```
=== ClawSync Meeting Negotiator ===
服务端地址: http://localhost:8000
轮询间隔: 10000ms
自动响应: 开启
轮询状态: 运行中
已绑定邮箱: alice@example.com
用户 ID: 1
绑定 Session: agent:main:webchat:dm:alice
```

## 使用示例

```
用户: 帮我绑定邮箱 alice@example.com
Agent → bind_identity(email: "alice@example.com")
       → Token 存本地, 后台轮询启动, session 已捕获

用户: 帮我约 Bob 和 Charlie 明天开半小时的架构讨论会
Agent → initiate_meeting(title: "架构讨论会", invitees: [...], duration_minutes: 30)
       → 服务端创建会议, 状态 COLLECTING

(后台每10秒自动轮询)
  发现 INITIAL_SUBMIT → pushMessageToUser → Agent 被唤醒
  Agent → check_and_respond_tasks() 无参数 → 获取日历数据
  Agent → check_and_respond_tasks(meeting_id, "INITIAL", available_slots) → 提交

  发现 COUNTER_PROPOSAL → pushMessageToUser → Agent 展示给用户
  用户: 周五上午可以
  Agent → check_and_respond_tasks(meeting_id, "COUNTER", available_slots) → 提交
```

## 文件结构

```
clawsync/
├── index.ts                           # 插件入口 (register + session管理 + 轮询管理器 + 引导注入)
├── package.json                       # npm 包描述
├── openclaw.plugin.json               # 插件清单 (id, configSchema)
├── README.md                          # 开发文档（本文件）
├── GUIDE.md                           # 用户引导文档 (面向终端用户的功能说明)
├── DEMO.md                            # 完整演示流程文档
└── src/
    ├── tools/
    │   ├── bind-identity.ts           # Tool 1: 身份绑定 (含 onBindSuccess 回调)
    │   ├── initiate-meeting.ts        # Tool 2: 发起会议协商
    │   └── check-and-respond-tasks.ts # Tool 3: 拉取任务 + 提交响应 (融合 API 5+6)
    ├── types/
    │   └── index.ts                   # 全局类型定义 (TimeSlot, SessionContext 等)
    └── utils/
        ├── api-client.ts              # HTTP 客户端封装 (API 1/2/5/6)
        ├── polling-manager.ts         # 独立轮询管理器 (幂等启停/Agent通知/扩展钩子)
        ├── mock-calendar.ts           # MVP 模拟日历 (TimeSlot[] + string[] 两种格式)
        └── storage.ts                 # Token/偏好/Session 本地持久化
```
