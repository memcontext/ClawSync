# Meeting Coordinator API Server - 接口参考文档

> 供 Coordinator Agent 和 OpenClaw Plugin 团队联调使用
>
> Base URL: `http://127.0.0.1:8000`
>
> 版本: v1.0.0 | 更新日期: 2026-03-19

---

## 目录

- [通用说明](#通用说明)
- [API 1: 邮箱绑定/注册](#api-1-邮箱绑定注册)
- [API 2: 发起会议协商](#api-2-发起会议协商)
- [API 3: 查询会议详情](#api-3-查询会议详情)
- [API 4: 获取待办任务 (Plugin 轮询)](#api-4-获取待办任务)
- [API 5: 提交空闲时间与妥协响应](#api-5-提交空闲时间与妥协响应)
- [API 6: 我的会议列表](#api-6-我的会议列表)
- [API 7: Agent 获取待协调任务](#api-7-agent-获取待协调任务)
- [API 8: Agent 提交协调决策结果](#api-8-agent-提交协调决策结果)
- [状态机流转图](#状态机流转图)
- [错误码与响应格式](#错误码与响应格式)
- [种子数据与快速验证](#种子数据与快速验证)

---

## 通用说明

### 认证方式

Plugin 端接口（API 1-6）使用 Bearer Token 认证：

```
Authorization: Bearer sk-xxxxxxxxxxxx
```

Agent 端接口（API 7-8）当前无需认证（内部服务调用）。

### 统一响应格式

所有接口返回统一的 JSON 格式：

```json
{
    "code": 200,
    "message": "描述信息",
    "data": { ... }
}
```

错误响应同样遵循此格式（包括 400/401/404/422/500）。

### 时间槽格式约定

| 场景 | 格式 | 示例 |
|------|------|------|
| Plugin → Server (API 2, 5) | 字符串 | `"2026-03-18 14:00-18:00"` |
| Server → Agent (API 7) | 字典 | `{"start": "2026-03-18 14:00", "end": "2026-03-18 18:00"}` |
| Agent → Server (API 8) | 字符串 | `"2026-03-18 15:00-15:30"` (仅 final_time) |

---

## API 1: 邮箱绑定/注册

**`POST /api/auth/bind`**

客户端首次运行时调用，获取长期有效的身份 Token。

**请求体:**

```json
{
    "email": "alice@example.com"
}
```

**响应体:**

```json
{
    "code": 200,
    "message": "注册成功",
    "data": {
        "token": "sk-abc123xyz...",
        "user_id": 1
    }
}
```

> 如果邮箱已注册，返回已有的 token，message 为 "用户已存在"。

---

## API 2: 发起会议协商

**`POST /api/meetings`** — 需要 Token

发起人创建会议并上报自己的空闲时间和偏好。

**请求体:**

```json
{
    "title": "项目架构讨论会",
    "duration_minutes": 30,
    "invitees": [
        "bob@example.com",
        "carol@example.com"
    ],
    "initiator_data": {
        "available_slots": [
            "2026-03-18 14:00-18:00",
            "2026-03-19 10:00-12:00"
        ],
        "preference_note": "尽量安排在下午，我不喜欢背靠背开会。"
    }
}
```

**响应体:**

```json
{
    "code": 200,
    "message": "会议协商已发起，等待受邀人响应",
    "data": {
        "meeting_id": "mtg_8899aabb",
        "title": "项目架构讨论会",
        "status": "COLLECTING",
        "duration_minutes": 30,
        "invitees": ["bob@example.com", "carol@example.com"],
        "initiator_data": {
            "available_slots": ["2026-03-18 14:00-18:00", "2026-03-19 10:00-12:00"],
            "preference_note": "尽量安排在下午，我不喜欢背靠背开会。"
        }
    }
}
```

> 受邀人如果不存在会自动注册。状态从 PENDING → COLLECTING。

---

## API 3: 查询会议详情

**`GET /api/meetings/{meeting_id}`** — 需要 Token

查询会议当前状态、参与者信息、最终时间。

**响应体:**

```json
{
    "code": 200,
    "message": "success",
    "data": {
        "meeting_id": "mtg_8899aabb",
        "title": "项目架构讨论会",
        "status": "CONFIRMED",
        "round_count": 0,
        "final_time": "2026-03-18 15:00-15:30",
        "coordinator_reasoning": "所有参与者在 15:00 都有空...",
        "participants": [
            {
                "email": "alice@example.com",
                "role": "initiator",
                "has_submitted": true,
                "latest_slots": ["2026-03-18 14:00-18:00"],
                "preference_note": "尽量安排在下午"
            },
            {
                "email": "bob@example.com",
                "role": "participant",
                "has_submitted": true,
                "latest_slots": ["2026-03-18 15:00-17:00"],
                "preference_note": null
            }
        ]
    }
}
```

---

## API 4: 获取待办任务

**`GET /api/tasks/pending`** — 需要 Token

**Plugin 核心轮询接口** — OpenClaw 后台每 1 分钟调用一次。

**响应体:**

```json
{
    "code": 200,
    "message": "success",
    "data": {
        "pending_tasks": [
            {
                "meeting_id": "mtg_8899aabb",
                "title": "项目架构讨论会",
                "initiator": "alice@example.com",
                "task_type": "INITIAL_SUBMIT",
                "message": "alice@example.com 邀请您参加会议，请提供您的空闲时间。",
                "duration_minutes": 30,
                "round_count": 0
            },
            {
                "meeting_id": "mtg_7766ccdd",
                "title": "代码 Review",
                "initiator": "dave@example.com",
                "task_type": "COUNTER_PROPOSAL",
                "message": "Bob 只有下午才有空，您能否将时间延长到 13:00-14:00？",
                "duration_minutes": 30,
                "round_count": 1
            }
        ]
    }
}
```

**task_type 说明:**

| task_type | 含义 | Plugin 行为 |
|-----------|------|------------|
| `INITIAL_SUBMIT` | 首次提交时间 | 读取日历，自动上报空闲时间 |
| `COUNTER_PROPOSAL` | 协商妥协请求 | 将 `message` 交给本地大模型或弹窗询问用户 |

---

## API 5: 提交空闲时间与妥协响应

**`POST /api/meetings/{meeting_id}/submit`** — 需要 Token

**请求体:**

```json
{
    "response_type": "INITIAL",
    "available_slots": [
        "2026-03-18 15:00-17:00"
    ],
    "preference_note": "3点之前有事"
}
```

**response_type 枚举:**

| 值 | 含义 | 说明 |
|---|------|------|
| `INITIAL` | 首次提交时间 | 必须包含 `available_slots` |
| `NEW_PROPOSAL` | 提交新方案 | 在协商轮次中提交新的时间，必须包含 `available_slots` |
| `ACCEPT_PROPOSAL` | 接受妥协方案 | 无需 `available_slots` |
| `REJECT` | 拒绝方案 | 会议直接终止为 FAILED |

**响应体 (INITIAL/NEW_PROPOSAL):**

```json
{
    "code": 200,
    "message": "提交成功，已触发服务端协调 Agent 重新计算。",
    "data": {
        "meeting_id": "mtg_8899aabb",
        "response_type": "INITIAL",
        "status": "ANALYZING",
        "all_submitted": true
    }
}
```

> 当 `all_submitted=true` 时，状态自动转为 ANALYZING，等待 Agent 轮询。

**响应体 (REJECT):**

```json
{
    "code": 200,
    "message": "已拒绝方案，会议协商终止",
    "data": {
        "meeting_id": "mtg_8899aabb",
        "response_type": "REJECT",
        "status": "FAILED"
    }
}
```

---

## API 6: 我的会议列表

**`GET /api/meetings`** — 需要 Token

返回当前用户参与的所有会议（包括发起的和受邀的）。

**响应体:**

```json
{
    "code": 200,
    "message": "success",
    "data": {
        "total": 2,
        "meetings": [
            {
                "meeting_id": "mtg_8899aabb",
                "title": "项目架构讨论会",
                "status": "COLLECTING",
                "my_role": "initiator",
                "action_required": false,
                "initiator_email": "alice@example.com",
                "duration_minutes": 30,
                "round_count": 0,
                "final_time": null,
                "progress": "1/3",
                "created_at": "2026-03-18T10:00:00"
            }
        ]
    }
}
```

---

## API 7: Agent 获取待协调任务

**`GET /api/agent/tasks/pending`** — 无需认证

**Agent 核心轮询接口** — Coordinator Agent 定时调用，获取所有 ANALYZING 状态的会议。

**响应体:**

```json
{
    "code": 200,
    "message": "success",
    "data": {
        "pending_tasks": [
            {
                "meeting_id": "mtg_seed_002",
                "title": "技术方案评审",
                "duration_minutes": 30,
                "round_count": 0,
                "participants_data": [
                    {
                        "user_id": 4,
                        "email": "dave@example.com",
                        "role": "initiator",
                        "latest_slots": [
                            {"start": "2026-03-21 10:00", "end": "2026-03-21 12:00"},
                            {"start": "2026-03-21 14:00", "end": "2026-03-21 16:00"}
                        ],
                        "preference_note": "尽量安排在上午"
                    },
                    {
                        "user_id": 1,
                        "email": "alice@example.com",
                        "role": "participant",
                        "latest_slots": [
                            {"start": "2026-03-21 10:00", "end": "2026-03-21 11:00"}
                        ],
                        "preference_note": "10点到11点最好"
                    }
                ]
            }
        ]
    }
}
```

> **注意:** `latest_slots` 为 `{start, end}` 字典格式，已由 Server 自动转换。

---

## API 8: Agent 提交协调决策结果

**`POST /api/agent/meetings/{meeting_id}/result`** — 无需认证

Agent 完成 LLM 推理后，调用此接口将决策写回数据库。

### 场景 A: 时间匹配成功 → CONFIRMED

```json
{
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-21 10:00-10:30",
    "agent_reasoning": "所有参与者在 10:00-11:00 都有空，选取前 30 分钟。",
    "counter_proposals": []
}
```

### 场景 B: 时间冲突 → NEGOTIATING

```json
{
    "decision_status": "NEGOTIATING",
    "final_time": null,
    "agent_reasoning": "Alice 和 Bob 的时间完全没有交集。",
    "counter_proposals": [
        {
            "target_email": "alice@example.com",
            "message": "Bob 只有下午 17:00 后才有空，您能否延长空闲时间？"
        }
    ]
}
```

> Server 会将 `counter_proposals` 中的 `message` 写入对应用户的待办任务，供 Plugin 拉取。
> 所有参与者的 `action_required` 都会被设为 `true`。
> `round_count` 自动 +1，超过最大轮数 (3) 自动转为 FAILED。

### 场景 C: 协商失败 → FAILED

```json
{
    "decision_status": "FAILED",
    "final_time": null,
    "agent_reasoning": "经过 3 轮协商，参与者依然无法达成一致。",
    "counter_proposals": []
}
```

### 统一响应体:

```json
{
    "code": 200,
    "message": "协调结果已成功应用，系统状态已更新",
    "data": {
        "meeting_id": "mtg_seed_002",
        "new_status": "CONFIRMED"
    }
}
```

---

## 状态机流转图

```
创建会议
    │
    ▼
 PENDING ──发出邀请──▶ COLLECTING ──全员提交──▶ ANALYZING
                           ▲                       │
                           │                   Agent 分析
                           │                 ┌─────┴─────┐
                           │            无冲突│           │存在冲突
                           │                 ▼           ▼
                           │           CONFIRMED    NEGOTIATING
                           │                         │    │
                           │            全员重新提交──┘    │超过最大轮数
                           │                              ▼
                           │                           FAILED
                           │
                     Plugin REJECT ─────────────────▶ FAILED
```

**状态说明:**

| 状态 | 含义 | 触发方 |
|------|------|--------|
| `PENDING` | 已创建，未发出邀请 | 创建时自动设置 |
| `COLLECTING` | 等待参与者提交时间 | 创建后自动流转 |
| `ANALYZING` | 全员已提交，等待 Agent 分析 | 最后一人提交后自动流转 |
| `NEGOTIATING` | 存在冲突，等待参与者妥协 | Agent 提交 NEGOTIATING 决策 |
| `CONFIRMED` | 协商成功，已确定时间 | Agent 提交 CONFIRMED 决策 |
| `FAILED` | 协商失败 | Agent 提交 / 用户 REJECT / 超过轮数 |

---

## 错误码与响应格式

| HTTP 状态码 | code | 含义 |
|------------|------|------|
| 200 | 200 | 成功 |
| 400 | 400 | 请求参数错误 / 状态不允许 |
| 401 | 401 | Token 无效 |
| 403 | 403 | 无权限（非会议参与者） |
| 404 | 404 | 资源不存在 |
| 422 | 422 | 请求体校验失败 |
| 500 | 500 | 服务器内部错误 |

所有错误响应格式：

```json
{
    "code": 400,
    "message": "当前会议状态为 CONFIRMED，不允许提交",
    "data": null
}
```

---

## 种子数据与快速验证

### 初始化

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 生成种子数据（会自动重建数据库）
python seed_data.py

# 3. 启动服务
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 预置数据

| 会议 ID | 状态 | 用途 |
|---------|------|------|
| `mtg_seed_001` | COLLECTING | Plugin 测试提交时间 (bob/carol 待提交) |
| `mtg_seed_002` | ANALYZING | Agent 测试轮询 + 提交决策结果 |
| `mtg_seed_003` | CONFIRMED | 查询已完成会议 |

### 预置用户

运行 `python seed_data.py` 后会打印所有用户的 Token，用于联调测试。

### 完整测试

```bash
# 运行自动化测试（覆盖全部接口和 4 种端到端场景）
python test_api.py
```
