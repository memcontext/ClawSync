# ClawMeeting 跨平台适配技术调研

> 日期：2026-04-07
> 范围：7 个平台 — QClaw、Kimi Claw、AutoClaw、Coze、MiniMax Agent、DeerFlow、百炼
> 目标：评估适配难度，明确开发工作量，确定优先级

---

## 1. 平台分类

按底层架构可分为三类：

| 类别 | 平台 | 含义 |
|------|------|------|
| **OpenClaw 原生** | QClaw（腾讯）、Kimi Claw（月之暗面）、AutoClaw（智谱） | 插件直接运行，零或接近零的适配成本 |
| **MCP 兼容** | Coze（字节）、MiniMax Agent、DeerFlow | 需要将插件封装为 MCP Server |
| **纯云平台** | 百炼（阿里） | 没有本地 Agent 运行时，本质是重写 |

---

## 2. 技术对比总表

| 维度 | QClaw（腾讯） | Kimi Claw（月之暗面） | AutoClaw（智谱） | Coze / 扣子（字节） | MiniMax Agent | DeerFlow（字节开源） | 百炼（阿里） |
|------|-------------|-------------------|----------------|-------------------|---------------|---------------------|-------------|
| **架构** | Electron 桌面应用，内置 OpenClaw | Web 端（kimi.com），原生 OpenClaw | 桌面应用，内置 OpenClaw | Web 平台 + Coze Studio（本地） | 桌面应用（非 OpenClaw） | Python 框架（LangGraph） | 云 API 平台 |
| **插件体系** | OpenClaw 插件 | OpenClaw 插件（ClawHub 5000+ Skills） | OpenClaw 插件（50+ Skills） | REST API 插件 + MCP | MCP 扩展 | MCP + Markdown Skills | API Agent + MCP |
| **MCP 支持** | 是（继承自 OpenClaw） | 是（CLI 原生） | 是（继承自 OpenClaw） | 是（Coze Space） | 是（原生） | 是（原生） | 是（Qwen3 原生） |
| **IM 渠道** | 企微、腾讯会议 | 仅 Web，无 IM | **飞书** | 飞书/企微/微信公众号/QQ/钉钉/Telegram/Slack/Discord | 无 | 无 | 钉钉 |
| **开源** | 否 | 否（基于开源 OpenClaw） | 是 | 部分（Coze Studio） | 部分（OpenRoom） | 是，Apache 2.0（50K+ stars） | 否（模型开源） |
| **状态** | 已发布 | 已发布 | 已发布（2026-03-24） | 已发布（Coze 2.0，2026 年 1 月） | 已发布 | 活跃（2026 年 2 月 GitHub Trending #1） | 已发布（云服务） |
| **适配难度** | 无 | 极低 | 极低 | 中等 | 中高 | 中等 | 高 |
| **当前适配度** | 已验证可用 | 0%（未测试，预期兼容） | 0%（未测试，预期兼容） | 0% | 0% | 0% | 0% |

---

## 3. 逐平台分析

### 3.1 QClaw（腾讯）— 已适配

- **与 OpenClaw 的关系**：Electron 外壳，内置完整 OpenClaw 实例
- **现状**：已验证可用
- **凭证共享**：QClaw 内置的 OpenClaw 和独立安装的 OpenClaw 都读取 `~/.openclaw/clawmeeting/credentials.json`，凭证自动迁移
- **额外能力**：企微渠道（通过 `wechat-access` 插件）、腾讯会议、腾讯文档、腾讯问卷
- **待做**：无

### 3.2 Kimi Claw（月之暗面）— 适配难度：极低

- **与 OpenClaw 的关系**：kimi.com 在浏览器端原生运行 OpenClaw
- **为什么难度低**：完整的 OpenClaw 插件 API 兼容 — `register(api)`、`registerTool()`、`registerService()`、`before_prompt_build` 全部可用
- **风险**：
  - 运行在**服务端/浏览器沙箱**中 — `~/.openclaw/clawmeeting/` 本地文件存储可能不可写
  - 无 IM 渠道集成 — `message tool` 推送链路不可用，只有 webchat 的 `sessions_send`
  - `ensureAllConfig()` 写入 `openclaw.json` — 可能与 Kimi 的托管配置冲突
- **需要开发**：
  - 在 kimi.com 上安装测试 — 验证 `register(api)` API 接口是否一致
  - 验证存储路径是否可写（若沙箱化则需适配 Kimi 的云存储）
  - 验证 `sessions_send` 和 `message tool` 在 Web 环境下的行为
  - 考虑发布到 ClawHub 获取曝光（5000+ Skill 生态）
- **预估工作量**：1-2 天（测试 + 小修）

### 3.3 AutoClaw / 澳龙（智谱）— 适配难度：极低

- **与 OpenClaw 的关系**：桌面安装包，打包了 OpenClaw + 智谱的 Pony-Alpha-2 模型
- **为什么难度低**：与 QClaw 相同的 OpenClaw 插件架构
- **优势**：
  - 内置**飞书集成** — 与 ClawMeeting 的飞书推送天然契合
  - 开源 — 可以看源码确认兼容性
  - 支持第三方模型 API（DeepSeek、Kimi、MiniMax）
- **风险**：
  - AutoClaw 可能有自定义配置结构 — `ensureAllConfig()` 自动写入 `openclaw.json` 可能需要路径调整
  - 三级渠道发现依赖特定文件路径（`~/.openclaw/credentials/`、`~/.openclaw/logs/commands.log`、`~/.openclaw/agents/main/sessions/`）— 需验证这些路径在 AutoClaw 的目录布局中是否存在
- **需要开发**：
  - 安装 AutoClaw，加载 ClawMeeting 插件，验证完整流程
  - 测试飞书渠道三级自动发现（Level 1/2/3）在 AutoClaw 环境下是否正常
  - 验证 `gateway.tools.allow` 自动配置的兼容性
- **预估工作量**：1-2 天（测试 + 小修）

### 3.4 Coze / 扣子（字节）— 适配难度：中等

- **与 OpenClaw 的关系**：完全独立的平台，非 OpenClaw 体系
- **为什么中等难度**：插件模型是无状态 REST API，与 OpenClaw 的有状态本地插件根本不同
- **优势**：
  - **渠道覆盖最广**：飞书/企微/微信公众号/QQ/钉钉/Telegram/Slack/Discord — 适配后用户触达最大
  - Coze 2.0 的 Coze Space 支持 MCP
  - Coze Studio 开源 — 可本地开发调试
  - Coze 原生处理渠道路由 — 插件不需要 `pushToExtraChannels` 逻辑
- **架构差异对照**：

  | OpenClaw 概念 | Coze 对应 | 差异 |
  |--------------|-----------|------|
  | `register(api)` | Coze 插件清单 + HTTP 端点 | 入口完全重写 |
  | `registerTool()` | 每个 tool 一个 REST API 端点 | 将 tool 封装为 HTTP handler |
  | `registerService()` / `gateway_start` | 无（无状态） | 需外部 cron 或 Coze Workflow 定时器 |
  | `before_prompt_build` | Persona / System Prompt 配置 | 静态配置，无动态注入 |
  | `message tool` 推送 | Coze 原生渠道路由 | 不需要 — Coze 自己处理 |
  | `~/.openclaw/` 存储 | Coze 插件存储 API 或外部数据库 | 需要迁移 |
  | 后台轮询 | Coze Workflow 定时触发 | 需重新设计轮询为 Workflow |

- **需要开发**：
  - **方案 A — MCP Server**：将 5 个 tool 封装为 MCP Server，通过 Coze Space MCP 集成接入
  - **方案 B — Coze 原生插件**：将 5 个 tool 暴露为 REST 端点，注册到 Coze 插件商店
  - 重新设计后台轮询：Coze Workflow 定时触发 → 调用 API → 处理任务
  - 本地文件存储替换为 Coze 插件存储或外部数据库
  - `before_prompt_build` 替换为 Coze Persona/Prompt 配置
  - 移除所有推送逻辑 — Coze 的多渠道部署自行处理通知投递
- **预估工作量**：1-2 周（MCP Server 路线）；2-3 周（原生 Coze 插件路线）

### 3.5 MiniMax Agent — 适配难度：中高

- **与 OpenClaw 的关系**：独立桌面 Agent 应用，仅支持 MCP 扩展
- **为什么中高难度**：MCP 是唯一扩展路径，且缺少生命周期钩子和推送机制
- **优势**：
  - M2.5 模型的多步工具协调能力强（Shell + Browser + Python + MCP 协同）
  - 桌面应用 — 本地文件系统可用
- **局限**：
  - 无 IM 渠道集成 — 通知仅在用户主动查询时可见
  - MCP 是请求-响应模式 — 没有守护进程生命周期来跑后台轮询
  - 无 `sessions_send` 或 `message tool` 等价物 — 无法主动推送到用户
- **需要开发**：
  - 将 ClawMeeting 封装为 MCP Server（5 个 tool → 5 个 MCP tool）
  - 后台轮询：作为常驻 MCP Server 进程，内部跑定时器
  - 推送机制：**根本性缺失** — 只能在 agent 查询工具时返回数据。考虑使用 MCP Notifications 规范（如客户端支持），或"查询时附带未读通知"模式
  - 去重和 Session 管理：在 MCP Server 进程内存中维护
  - 存储：可使用本地文件系统（桌面应用）
- **预估工作量**：1-2 周（MCP Server）+ 推送通知的 UX 妥协

### 3.6 DeerFlow（字节开源）— 适配难度：中等

- **与 OpenClaw 的关系**：Python 框架（基于 LangGraph），非桌面应用
- **为什么中等难度**：原生支持 MCP，但需要语言/架构切换
- **优势**：
  - 50K+ GitHub stars，社区非常活跃
  - SuperAgent 模式（supervisor + sub-agents）— 天然适合多方会议协商场景
  - Skill 格式（Markdown + YAML frontmatter）与 OpenClaw 的 SKILL.md 概念相似
  - 本地执行 — 完整文件系统访问
  - 有 Message Gateway 功能
- **架构差异对照**：

  | OpenClaw 概念 | DeerFlow 对应 | 差异 |
  |--------------|-------------|------|
  | TypeScript 插件 | Python 工具类或 MCP Server | 语言移植或 MCP 桥接 |
  | `registerTool()` | `@tool` 装饰器或 MCP tool 注册 | 映射直接 |
  | 后台轮询 | Python asyncio 定时器 | 可实现 |
  | `before_prompt_build` | Skill YAML + supervisor prompt | 注入模型不同 |
  | `message tool` 推送 | Message Gateway | 需调研 |

- **需要开发**：
  - **方案 A — MCP Server**（推荐）：复用为 Coze/MiniMax 构建的同一个 MCP Server，接入 DeerFlow 的 MCP 配置
  - **方案 B — 原生 DeerFlow Skill**：将 API Client 移植到 Python，实现为 LangChain 工具类，编写 Markdown Skill 描述
  - 后台轮询：MCP Server 内 Python asyncio 任务或 DeerFlow 自定义服务
  - 调研 Message Gateway 用于推送通知
- **预估工作量**：1 周（复用 MCP Server）或 2-3 周（原生 Python 移植）

### 3.7 百炼（阿里）— 适配难度：高

- **与 OpenClaw 的关系**：纯云平台，没有本地 Agent 运行时
- **为什么难度高**：没有桌面应用，没有本地插件系统 — 本质是在云基础设施上全面重写
- **局限**：
  - 没有 `register(api)` 入口 — 必须在百炼控制台从零构建 Agent 应用
  - 没有本地文件系统 — 所有存储需迁移到云服务（OSS、TableStore 等）
  - 主要 IM 渠道仅钉钉
  - Qwen3 的 MCP/function-calling 能力很强，但平台封装限制较多
- **需要开发**：
  - 在百炼平台构建 Agent 应用，注册 5 个 function-calling 工具
  - 存储迁移到阿里云服务（OSS / TableStore / RDS）
  - 后台轮询改用阿里云函数计算（FC）定时触发
  - 推送通知走钉钉 webhook / 钉钉机器人 API
  - 完全重写初始化、生命周期、Session 管理
- **预估工作量**：3-4 周（完全重建）

---

## 4. 适配策略

### 第一阶段 — OpenClaw 家族（接近零成本）

在 2 个未测试的 OpenClaw 原生平台上验证 ClawMeeting：

```
QClaw（已完成）→ AutoClaw（测试）→ Kimi Claw（测试）
```

预计工作量：共 2-4 天。完成后发布到 ClawHub 进行 Kimi Claw 分发。

### 第二阶段 — MCP Server（一次开发，三个平台）

构建标准 MCP Server 封装 5 个 ClawMeeting 工具：

```
ClawMeeting MCP Server → Coze Space / MiniMax Agent / DeerFlow
```

MCP Server 应具备：
- 暴露 5 个工具：`bind_identity`、`verify_email_code`、`initiate_meeting`、`check_and_respond_tasks`、`list_meetings`
- 作为常驻进程运行，内部跑轮询定时器
- 本地处理凭证存储
- 提供 MCP Notifications 用于主动推送（在支持的客户端上）

预计工作量：MCP Server 开发 1-2 周，之后每个平台对接约 2 天。

### 第三阶段 — 云平台（按需）

百炼适配仅在有明确钉钉用户业务需求时才做。这是完全重建，不是移植。

---

## 5. 优先级矩阵

| 优先级 | 平台 | 工作量 | 价值 | 理由 |
|--------|------|--------|------|------|
| **P0** | QClaw（腾讯） | 已完成 | 高 | 已验证可用，企微渠道 |
| **P1** | AutoClaw（智谱） | 1-2 天 | 高 | OpenClaw 原生 + 飞书集成，开源 |
| **P1** | Kimi Claw（月之暗面） | 1-2 天 | 高 | OpenClaw 原生 + ClawHub 5000+ 用户池 |
| **P2** | Coze / 扣子（字节） | 1-2 周 | 极高 | 渠道覆盖最广（8+ IM 平台） |
| **P3** | DeerFlow | 1 周 | 中 | 大型开源社区，MCP Server 可复用 P2 成果 |
| **P3** | MiniMax Agent | 1 周 | 低 | 无 IM 渠道，推送能力受限 |
| **P4** | 百炼（阿里） | 3-4 周 | 低 | 完全重建，仅钉钉，ROI 最低 |

---

## 6. 核心结论

国内 AI Agent 生态正在向两个标准收敛：

1. **OpenClaw** — 用于桌面/Web Agent 应用（QClaw、Kimi Claw、AutoClaw）
2. **MCP** — 用于工具/插件互操作性（Coze、MiniMax、DeerFlow、百炼）

ClawMeeting 当前的 OpenClaw 插件天然覆盖第一类。构建一个 MCP Server 即可解锁第二类。两个产物合计覆盖全部 7 个平台。
