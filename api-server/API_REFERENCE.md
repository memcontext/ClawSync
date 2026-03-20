# Meeting Coordinator API Server - 接口参考文档

> 供 Coordinator Agent 和 OpenClaw Plugin 团队联调使用
>
> Base URL: `http://39.105.143.2:7010`
>
> 版本: v1.3.0 | 更新日期: 2026-03-20

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
- [更新日志](#更新日志)

---

## 通用说明

### 认证方式

Plugin 端接口（API 1-6）支持两种认证方式：

```
方式一（生产环境）: Authorization: Bearer sk-xxxxxxxxxxxx
方式二（Swagger 测试）: ?token=sk-xxxxxxxxxxxx
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
| Agent → Server (API 8) | 字符串 | `"2026-03-18 15:00-15:30"` (final_time 和 suggested_slots) |
| Server → Plugin (API 4) | 字符串 | `"2026-03-18 17:00-18:00"` (suggested_slots) |

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
        "id": "mtg_8899aabb",
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

**Plugin 核心轮询接口** — OpenClaw 后台定时调用。

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
                "suggested_slots": [],
                "duration_minutes": 30,
                "round_count": 0
            },
            {
                "meeting_id": "mtg_7766ccdd",
                "title": "代码 Review",
                "initiator": "dave@example.com",
                "task_type": "COUNTER_PROPOSAL",
                "message": "Bob 只有下午才有空，建议您调整到以下时间段",
                "suggested_slots": ["2026-03-18 17:00-18:00", "2026-03-19 14:00-16:00"],
                "duration_minutes": 30,
                "round_count": 1
            }
        ]
    }
}
```

**task_type 说明:**

| task_type | 含义 | suggested_slots | Plugin 行为 |
|-----------|------|-----------------|------------|
| `INITIAL_SUBMIT` | 首次提交时间 | `[]` 空 | 读取日历，自动上报空闲时间 |
| `COUNTER_PROPOSAL` | 协商妥协请求 | 包含 Agent 建议的时间槽 | 将 `message` 和 `suggested_slots` 展示给用户，用户选择接受或提供新时间 |
| `MEETING_CONFIRMED` | 会议已确认 | `[]` 空 | 通知用户会议已确认及最终时间 |
| `MEETING_FAILED` | 协商失败 | `[]` 空 | 通知用户协商失败及原因 |

> **v1.1 新增:** `suggested_slots` 字段 — 多轮协商中 Agent 建议的调整时间槽。

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
| `COUNTER` | 提交新方案（插件别名） | 等同于 `NEW_PROPOSAL`，插件兼容 |
| `ACCEPT_PROPOSAL` | 接受妥协方案 | 无需 `available_slots` |
| `REJECT` | 拒绝会议/方案 | COLLECTING 或 NEGOTIATING 阶段均可使用，会议直接终止为 FAILED，通知所有其他参与者 |

**响应体 (INITIAL/NEW_PROPOSAL/COUNTER):**

```json
{
    "code": 200,
    "message": "提交成功，已触发服务端协调 Agent 重新计算。",
    "data": {
        "id": "mtg_8899aabb",
        "meeting_id": "mtg_8899aabb",
        "response_type": "INITIAL",
        "status": "ANALYZING",
        "all_submitted": true
    }
}
```

> 当 `all_submitted=true` 时，状态自动转为 ANALYZING，等待 Agent 轮询。
> 多轮协商中，只有被 Agent 点名的用户提交后才会触发 all_submitted 检查。

**响应体 (REJECT):**

> **v1.3 更新:** REJECT 现在支持在 COLLECTING 阶段使用（拒绝会议邀请），不再仅限于 NEGOTIATING 阶段。
> 拒绝后会自动通知所有其他参与者会议已失败。

```json
{
    "code": 200,
    "message": "已拒绝，会议协商终止",
    "data": {
        "id": "mtg_8899aabb",
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
                "max_rounds": 3,
                "previous_reasoning": null,
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

> **注意:**
> - `latest_slots` 为 `{start, end}` 字典格式，已由 Server 自动转换。
> - `round_count > 0` 表示这是多轮协商中的重新分析。
> - **v1.2 新增:** `max_rounds` — Agent 可用此字段判断剩余协商次数（`max_rounds - round_count`），在最后一轮做更激进的妥协。
> - **v1.2 新增:** `previous_reasoning` — 上一轮 Agent 的分析结论，第 1 轮为 `null`。Agent 可参考上一轮分析避免重复建议。

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

**Server 行为:**
- 状态 ANALYZING → CONFIRMED
- 所有参与者 `action_required=True`，写入确认通知
- Plugin 轮询时收到 `MEETING_CONFIRMED` 任务

### 场景 B: 时间冲突 → NEGOTIATING → COLLECTING（多轮协商）

```json
{
    "decision_status": "NEGOTIATING",
    "final_time": null,
    "agent_reasoning": "Alice 只有上午有空，Bob 只有下午有空，完全没有交集。",
    "counter_proposals": [
        {
            "target_email": "alice@example.com",
            "message": "Bob 只有下午 17:00 后才有空，建议您调整到以下时间段",
            "suggested_slots": ["2026-03-18 17:00-18:00", "2026-03-19 14:00-16:00"]
        },
        {
            "target_email": "bob@example.com",
            "message": "Alice 上午 10:00-12:00 有空，建议您考虑上午时段",
            "suggested_slots": ["2026-03-18 10:00-12:00"]
        }
    ]
}
```

**Server 行为（v1.1 更新）:**
- 状态 ANALYZING → NEGOTIATING → **COLLECTING**（两步流转）
- **只有** `counter_proposals` 中 `target_email` 对应的用户 `action_required=True`
- 未被点名的用户保持 `action_required=False`（不需要重新提交）
- `suggested_slots` 存入数据库，Plugin 轮询时可获取
- `round_count` 自动 +1，超过最大轮数 (3) 自动转为 FAILED
- 被点名的用户重新提交后 → ANALYZING → Agent 再次分析 → 循环

**counter_proposals 字段说明:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `target_email` | string | 需要调整时间的用户邮箱 |
| `message` | string | Agent 给用户的协商建议文字 |
| `suggested_slots` | string[] | **v1.1 新增** Agent 建议的调整时间槽 |

### 场景 C: 协商失败 → FAILED

```json
{
    "decision_status": "FAILED",
    "final_time": null,
    "agent_reasoning": "经过 3 轮协商，参与者依然无法达成一致。",
    "counter_proposals": []
}
```

**Server 行为:**
- 状态 ANALYZING → FAILED
- 所有参与者 `action_required=True`，写入失败通知
- Plugin 轮询时收到 `MEETING_FAILED` 任务

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
                         ▲    ▲                    │
                         │    │                Agent 分析
                         │    │              ┌─────┴─────┐
                         │    │         无冲突│           │存在冲突
                         │    │              ▼           ▼
                         │    │        CONFIRMED    NEGOTIATING
                         │    │                      │    │
                         │    └──部分用户重新提交─────┘    │超过最大轮数
                         │                                ▼
                         │                             FAILED
                         │
                   Plugin REJECT ──────────────────▶ FAILED
```

**v1.1 关键变化:**
- NEGOTIATING 后状态回到 **COLLECTING**（不再停在 NEGOTIATING）
- 只有被 Agent 点名的用户需要重新提交
- 未被点名的用户不受影响

**状态说明:**

| 状态 | 含义 | 触发方 |
|------|------|--------|
| `PENDING` | 已创建，未发出邀请 | 创建时自动设置 |
| `COLLECTING` | 等待参与者提交时间 | 创建后自动流转 / Agent NEGOTIATING 后回到此状态 |
| `ANALYZING` | 全员已提交，等待 Agent 分析 | 最后一人提交后自动流转 |
| `NEGOTIATING` | 存在冲突（过渡状态） | Agent 提交 NEGOTIATING，随即转为 COLLECTING |
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

## 更新日志

### v1.3.0 (2026-03-20)

**COLLECTING 阶段支持 REJECT + 失败通知**

1. **REJECT 支持 COLLECTING 阶段** — 被邀请人可以在收集阶段直接拒绝会议邀请，不再需要先提交时间
2. **状态机新增 COLLECTING → FAILED 转换** — 支持上述拒绝场景
3. **REJECT 后通知其他参与者** — 所有其他参与者收到 `MEETING_FAILED` 类型的待办通知
4. **区分拒绝场景** — COLLECTING 阶段显示"拒绝了会议邀请"，NEGOTIATING 阶段显示"拒绝了协商方案"

### v1.2.0 (2026-03-20)

**Agent 轮询上下文增强**

1. **API 7 新增 `max_rounds` 字段** — Agent 知道最大协商轮数，可判断剩余机会
2. **API 7 新增 `previous_reasoning` 字段** — Agent 获取上一轮自己的分析结论，避免重复建议

### v1.1.0 (2026-03-20)

**多轮协商支持 + 建议时间槽**

1. **`CounterProposalItem` 新增 `suggested_slots` 字段** (API 8)
   - Agent 返回 NEGOTIATING 时可携带建议时间槽
   - 格式: `["2026-03-18 17:00-18:00", "2026-03-19 14:00-16:00"]`

2. **NEGOTIATING 后状态回到 COLLECTING** (状态机)
   - 旧: ANALYZING → NEGOTIATING（停住）
   - 新: ANALYZING → NEGOTIATING → COLLECTING（自动流转）

3. **精准标记需要重新提交的用户** (API 8)
   - 旧: 所有参与者 `action_required=True`
   - 新: 只有 `counter_proposals` 中 `target_email` 对应的用户需要操作

4. **Plugin 待办任务新增 `suggested_slots`** (API 4)
   - `COUNTER_PROPOSAL` 类型的任务现在包含 Agent 建议的时间槽
   - Plugin 可将建议时间展示给用户参考

5. **NegotiationLog 新增 `suggested_slots` 列数据库字段**
   - 需要删除旧数据库重建: `rm meeting_coordinator.db`

### v1.0.0 (2026-03-19)

- 初始版本：8 个 API 接口、6 状态机、Token 认证
