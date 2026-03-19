# Meeting Coordinator API 接口文档

> Base URL: `http://localhost:8000`
> 认证方式: `Authorization: Bearer <Token>`
> 版本: 1.0.0

---

## 一、通用说明

### 统一响应格式

所有接口返回统一的 JSON 结构：

```json
{
    "code": 200,
    "message": "描述信息",
    "data": { ... }
}
```

### 认证机制

MVP 阶段采用极简 Token 机制：
1. 客户端调用 `/api/auth/bind` 获取 Token
2. 后续所有请求在 HTTP Header 中携带 `Authorization: Bearer <Token>`

### 错误码

| HTTP 状态码 | 说明 |
|-------------|------|
| 200 | 成功 |
| 400 | 请求参数错误 / 业务逻辑错误（如非法状态流转） |
| 401 | Token 无效或缺失 |
| 403 | 无权限访问该资源 |
| 404 | 资源不存在 |
| 422 | 请求体格式校验失败 |
| 500 | 服务器内部错误 |

---

## 二、身份认证模块 (Auth)

### API 1: 邮箱绑定 / 注册

**POST** `/api/auth/bind`

接收用户邮箱，返回或生成一个长期有效的身份 Token。如果邮箱已注册，直接返回已有 Token。

**请求体:**

```json
{
    "email": "user@example.com"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string (email) | 是 | 用户邮箱地址 |

**响应体 (新用户):**

```json
{
    "code": 200,
    "message": "注册成功",
    "data": {
        "token": "sk-xxxxxxxxxxxxxxxx",
        "user_id": 1
    }
}
```

**响应体 (已有用户):**

```json
{
    "code": 200,
    "message": "用户已存在",
    "data": {
        "token": "sk-xxxxxxxxxxxxxxxx",
        "user_id": 1
    }
}
```

---

## 三、会议模块 (Meetings)

### API 2: 发起会议协商

**POST** `/api/meetings`

**需要认证:** 是

发起人调用此接口，创建会议并上报自己的空闲时间和偏好。受邀人如果未注册会被自动创建账号。

创建后会议状态自动从 `PENDING` 流转到 `COLLECTING`。

**请求体:**

```json
{
    "title": "项目架构讨论会",
    "duration_minutes": 30,
    "invitees": [
        "userB@example.com",
        "userC@example.com"
    ],
    "initiator_data": {
        "available_slots": [
            "2026-03-18 14:00-18:00",
            "2026-03-19 10:00-12:00"
        ],
        "preference_note": "尽量安排在下午"
    }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | string | 是 | 会议标题 |
| duration_minutes | integer | 是 | 会议时长（分钟） |
| invitees | string[] | 是 | 受邀人邮箱列表 |
| initiator_data.available_slots | string[] | 是 | 发起人可用时间段列表 |
| initiator_data.preference_note | string | 否 | 发起人的时间偏好说明 |

**响应体:**

```json
{
    "code": 200,
    "message": "会议协商已发起，等待受邀人响应",
    "data": {
        "id": "mtg_a1b2c3d4e5f67890",
        "title": "项目架构讨论会",
        "status": "COLLECTING",
        "duration_minutes": 30,
        "invitees": ["userB@example.com", "userC@example.com"],
        "initiator_data": {
            "available_slots": ["2026-03-18 14:00-18:00", "2026-03-19 10:00-12:00"],
            "preference_note": "尽量安排在下午"
        }
    }
}
```

---

### API 3: 我的会议列表

**GET** `/api/meetings`

**需要认证:** 是

获取当前用户参与的所有会议（包括自己发起的和被邀请的），按创建时间倒序排列。

**请求参数:** 无

**响应体:**

```json
{
    "code": 200,
    "message": "success",
    "data": {
        "total": 2,
        "meetings": [
            {
                "meeting_id": "mtg_a1b2c3d4e5f67890",
                "title": "项目架构讨论会",
                "status": "COLLECTING",
                "my_role": "initiator",
                "action_required": false,
                "initiator_email": "alice@example.com",
                "duration_minutes": 30,
                "round_count": 0,
                "final_time": null,
                "progress": "1/3",
                "created_at": "2026-03-18T14:00:00"
            }
        ]
    }
}
```

| 响应字段 | 说明 |
|----------|------|
| my_role | 当前用户角色：`initiator`（发起人）或 `participant`（参与者） |
| action_required | 当前用户是否需要操作（提交/重新提交时间） |
| progress | 提交进度，格式 "已提交/总人数" |

---

### API 4: 查询会议详情

**GET** `/api/meetings/{meeting_id}`

**需要认证:** 是（仅会议参与者可查看）

**路径参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| meeting_id | string | 会议 ID |

**响应体:**

```json
{
    "code": 200,
    "message": "success",
    "data": {
        "meeting_id": "mtg_a1b2c3d4e5f67890",
        "title": "项目架构讨论会",
        "status": "COLLECTING",
        "round_count": 0,
        "final_time": null,
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
                "has_submitted": false,
                "latest_slots": [],
                "preference_note": null
            }
        ]
    }
}
```

---

### API 5: 提交空闲时间

**POST** `/api/meetings/{meeting_id}/submit`

**需要认证:** 是

参与者提交自己的空闲时间段。仅在会议状态为 `COLLECTING` 或 `NEGOTIATING` 时允许提交。

当所有参与者都提交后，系统自动：
1. 状态流转到 `ANALYZING`
2. 调用 Coordinator 进行冲突分析
3. 根据分析结果自动流转到 `CONFIRMED`（无冲突）或 `NEGOTIATING`（有冲突）

**路径参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| meeting_id | string | 会议 ID |

**请求体:**

```json
{
    "response_type": "INITIAL",
    "available_slots": [
        {"start": "2026-03-18 14:00", "end": "2026-03-18 16:00"},
        {"start": "2026-03-19 10:00", "end": "2026-03-19 11:30"}
    ],
    "preference_note": "下午优先"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| response_type | string | 是 | 提交类型：`INITIAL`（首次）/ `COUNTER`（协商轮次） |
| available_slots | TimeSlot[] | 是 | 可用时间段列表 |
| available_slots[].start | string | 是 | 时间段开始时间 |
| available_slots[].end | string | 是 | 时间段结束时间 |
| preference_note | string | 否 | 时间偏好说明 |

**响应体 (部分提交):**

```json
{
    "code": 200,
    "message": "提交成功",
    "data": {
        "id": "mtg_a1b2c3d4e5f67890",
        "response_type": "INITIAL",
        "status": "COLLECTING",
        "all_submitted": false,
        "coordinator_result": null,
        "created_at": "2026-03-18T14:00:00",
        "updated_at": "2026-03-18T14:05:00"
    }
}
```

**响应体 (全员提交 → 无冲突 → CONFIRMED):**

```json
{
    "code": 200,
    "message": "提交成功",
    "data": {
        "id": "mtg_a1b2c3d4e5f67890",
        "response_type": "INITIAL",
        "status": "CONFIRMED",
        "all_submitted": true,
        "coordinator_result": {
            "status": "CONFIRMED",
            "final_time": "2026-03-18 14:00-2026-03-18 16:00",
            "reasoning": "找到共同空闲时间",
            "alternative_slots": []
        },
        "created_at": "2026-03-18T14:00:00",
        "updated_at": "2026-03-18T14:10:00"
    }
}
```

**响应体 (全员提交 → 有冲突 → NEGOTIATING):**

```json
{
    "code": 200,
    "message": "提交成功",
    "data": {
        "id": "mtg_a1b2c3d4e5f67890",
        "response_type": "INITIAL",
        "status": "NEGOTIATING",
        "all_submitted": true,
        "coordinator_result": {
            "status": "NEGOTIATING",
            "reasoning": "未找到共同空闲时间，需要进一步协商",
            "suggestions": ["建议考虑调整时间范围"]
        },
        "created_at": "2026-03-18T14:00:00",
        "updated_at": "2026-03-18T14:10:00"
    }
}
```

---

## 四、待办任务模块 (Tasks)

### API 6: 查询待办任务

**GET** `/api/tasks/pending`

**需要认证:** 是

获取当前用户需要处理的待办任务列表（未提交时间的会议邀请）。

**请求参数:** 无

**响应体:**

```json
{
    "code": 200,
    "message": "success",
    "data": {
        "pending_tasks": [
            {
                "meeting_id": "mtg_a1b2c3d4e5f67890",
                "title": "项目架构讨论会",
                "initiator": "alice@example.com",
                "task_type": "INITIAL_SUBMIT",
                "message": "请提交会议 '项目架构讨论会' 的空闲时间"
            }
        ]
    }
}
```

| 响应字段 | 说明 |
|----------|------|
| task_type | `INITIAL_SUBMIT`（首次提交）或 `COUNTER_PROPOSAL`（协商重新提交） |
| message | 给用户的操作提示 |

---

## 五、系统接口

### 健康检查

**GET** `/health`

```json
{
    "status": "healthy",
    "timestamp": "2026-03-18T14:00:00"
}
```

### 根路径

**GET** `/`

返回服务信息和可用端点列表。

---

## 六、会议状态机

```
    创建会议
       ↓
    PENDING
       ↓ 发出邀请
    COLLECTING
       ↓ 所有人已提交
    ANALYZING
      ↙     ↘
  无冲突    存在冲突
    ↓         ↓
CONFIRMED  NEGOTIATING ←→ ANALYZING
              ↓ 超过最大轮数(3)
           FAILED
```

| 状态 | 说明 |
|------|------|
| PENDING | 会议刚创建 |
| COLLECTING | 邀请已发出，等待参与者提交空闲时间 |
| ANALYZING | 所有参与者已提交，Coordinator 正在分析 |
| NEGOTIATING | 存在时间冲突，需要参与者重新提交 |
| CONFIRMED | 协商成功，已确定最终时间 |
| FAILED | 超过最大协商轮数（默认 3 轮），协商失败 |

---

## 七、数据库模型

### users (用户表)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, 自增 | 用户 ID |
| email | VARCHAR(255) | UK, NOT NULL | 邮箱 |
| token | VARCHAR(255) | UK, NOT NULL | 认证 Token |
| created_at | DATETIME | 默认当前时间 | 创建时间 |

### meetings (会议主表)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(50) | PK | 会议 ID (mtg_xxx) |
| initiator_id | INT | FK → users.id | 发起人 ID |
| title | VARCHAR(255) | NOT NULL | 会议标题 |
| duration_minutes | INT | NOT NULL | 会议时长（分钟） |
| status | VARCHAR(50) | NOT NULL | 会议状态 |
| final_time | DATETIME | 可空 | 最终确定时间 |
| round_count | INT | 默认 0 | 协商轮次 |
| created_at | DATETIME | 默认当前时间 | 创建时间 |
| updated_at | DATETIME | 自动更新 | 更新时间 |

### negotiation_logs (协商流转表)

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INT | PK, 自增 | 记录 ID |
| meeting_id | VARCHAR(50) | FK → meetings.id | 会议 ID |
| user_id | INT | FK → users.id | 用户 ID |
| role | VARCHAR(50) | NOT NULL | 角色 (initiator/participant) |
| latest_slots | JSON | NOT NULL | 最新提交的时间段 |
| preference_note | TEXT | 可空 | 偏好说明 |
| action_required | BOOL | 默认 True | 是否需要操作 |
| created_at | DATETIME | 默认当前时间 | 创建时间 |
| updated_at | DATETIME | 自动更新 | 更新时间 |
