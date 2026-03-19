# Meeting Coordinator - 联调测试指南

> 由 seed_data.py 自动生成于 2026-03-19 05:37:02 UTC

## 服务地址

- API Base URL: http://192.168.22.28:8000
- Swagger 文档: http://192.168.22.28:8000/docs
- 健康检查:     http://192.168.22.28:8000/health

## 认证方式

所有需要认证的接口支持两种方式传递 Token：

1. **Header 方式**（生产环境推荐）
   ```
   Authorization: Bearer sk-seed-alice
   ```

2. **Query 参数方式**（Swagger UI 测试推荐）
   ```
   GET /api/tasks/pending?token=sk-seed-alice
   ```

---

## 预置用户

| 邮箱 | Token | User ID |
|------|-------|---------|
| alice@example.com | `sk-seed-alice` | 1 |
| bob@example.com | `sk-seed-bob` | 2 |
| carol@example.com | `sk-seed-carol` | 3 |
| dave@example.com | `sk-seed-dave` | 4 |
| eve@example.com | `sk-seed-eve` | 5 |

---

## 预置会议

| 会议 ID | 状态 | 标题 | 发起人 | 参与者 | 测试用途 |
|---------|------|------|--------|--------|----------|
| mtg_seed_001 | COLLECTING | Sprint 14 规划会议 | alice | bob, carol(待提交) | Plugin 提交时间 |
| mtg_seed_002 | ANALYZING | 技术方案评审 | dave | alice, eve(已提交) | Agent 无冲突→CONFIRMED |
| mtg_seed_003 | ANALYZING | 产品需求对齐会 | alice | bob, carol(已提交) | Agent 有冲突→NEGOTIATING |
| mtg_seed_004 | CONFIRMED | 代码 Review 周会 | bob | alice, dave | 查询已完成会议 |

---

## Agent 团队测试步骤

### 步骤 1: 轮询待协调任务

```
GET /api/agent/tasks/pending
```

预期返回 mtg_seed_002 和 mtg_seed_003 两个任务，每个任务包含参与者的时间槽和偏好。

### 步骤 2: 对无冲突会议提交 CONFIRMED

```
POST /api/agent/meetings/mtg_seed_002/result
Content-Type: application/json

{
  "decision_status": "CONFIRMED",
  "final_time": "2026-03-21 10:00-10:30",
  "agent_reasoning": "三人都在 10:00-11:00 有空，选取前 30 分钟",
  "counter_proposals": []
}
```

预期：会议状态变为 CONFIRMED，final_time 被设置。

### 步骤 3: 对有冲突会议提交 NEGOTIATING

mtg_seed_003 中 alice 只有上午有空，bob 只有下午有空，时间完全冲突。

```
POST /api/agent/meetings/mtg_seed_003/result
Content-Type: application/json

{
  "decision_status": "NEGOTIATING",
  "final_time": null,
  "agent_reasoning": "alice 只有上午有空，bob 只有下午有空，完全没有交集",
  "counter_proposals": [
    {
      "target_email": "alice@example.com",
      "message": "bob 只有下午才有空，您能否将时间延长到 13:00？"
    },
    {
      "target_email": "bob@example.com",
      "message": "alice 只有上午有空，您能否提前到 12:00？"
    }
  ]
}
```

预期：会议状态变为 NEGOTIATING，各参与者收到妥协建议。

### 步骤 4: 验证妥协建议下发

```
GET /api/tasks/pending?token=sk-seed-alice
```

预期：alice 的待办任务中出现 task_type=COUNTER_PROPOSAL，message 包含 Agent 的建议。

---

## Plugin 团队测试步骤

### 步骤 1: 查询待办任务

```
GET /api/tasks/pending?token=sk-seed-bob
```

预期：bob 有 mtg_seed_001 的待办，task_type=INITIAL_SUBMIT。

### 步骤 2: bob 提交空闲时间

```
POST /api/meetings/mtg_seed_001/submit?token=sk-seed-bob
Content-Type: application/json

{
  "response_type": "INITIAL",
  "available_slots": ["2026-03-20 10:00-12:00"],
  "preference_note": "上午有空"
}
```

预期：提交成功，状态仍为 COLLECTING（carol 未提交）。

### 步骤 3: carol 提交（触发 ANALYZING）

```
POST /api/meetings/mtg_seed_001/submit?token=sk-seed-carol
Content-Type: application/json

{
  "response_type": "INITIAL",
  "available_slots": ["2026-03-20 14:00-16:00"],
  "preference_note": "下午有空"
}
```

预期：all_submitted=true，状态自动流转为 ANALYZING。

### 步骤 4: 查询会议详情

```
GET /api/meetings/mtg_seed_004?token=sk-seed-bob
```

预期：返回已完成会议的详情，包含 final_time 和 coordinator_reasoning。

---

## 接口速查表

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | /api/auth/bind | 无 | 邮箱注册/绑定，返回 Token |
| GET | /api/meetings | 需要 | 我的会议列表 |
| POST | /api/meetings | 需要 | 创建会议 |
| GET | /api/meetings/{id} | 需要 | 查询会议详情 |
| POST | /api/meetings/{id}/submit | 需要 | 提交时间/响应 |
| GET | /api/tasks/pending | 需要 | Plugin 待办任务 |
| GET | /api/agent/tasks/pending | 无 | Agent 轮询待协调任务 |
| POST | /api/agent/meetings/{id}/result | 无 | Agent 提交协调结果 |

---

## response_type 枚举说明

| 值 | 使用场景 | 是否需要 available_slots |
|----|---------|------------------------|
| INITIAL | 首次提交时间 | 是 |
| NEW_PROPOSAL | 协商后重新提交 | 是 |
| ACCEPT_PROPOSAL | 接受 Coordinator 建议 | 否 |
| REJECT | 拒绝继续协商 | 否 |

## decision_status 枚举说明（Agent 用）

| 值 | 含义 | 是否需要 final_time |
|----|------|-------------------|
| CONFIRMED | 找到共同时间 | 是 |
| NEGOTIATING | 有冲突，需妥协 | 否 |
| FAILED | 协商彻底失败 | 否 |
