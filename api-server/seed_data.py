"""
数据种子脚本 - 为 Agent / Plugin 团队联调预置测试数据

运行方式:
  python seed_data.py

⚠️ 运行前无需手动删除数据库，脚本会自动清空并重建。

预置数据:
  - 5 个用户 (alice/bob/carol/dave/eve)，Token 固定方便联调
  - 4 个会议，分别处于不同状态:
    * mtg_seed_001: COLLECTING  → Plugin 测试提交时间
    * mtg_seed_002: ANALYZING   → Agent 测试无冲突 → CONFIRMED
    * mtg_seed_003: ANALYZING   → Agent 测试有冲突 → NEGOTIATING
    * mtg_seed_004: CONFIRMED   → 查询已完成会议
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from app.models.database import Base, engine, SessionLocal, User, Meeting, NegotiationLog

# ========== 初始化数据库 ==========

print("正在清空并重建数据库...")
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

db = SessionLocal()

try:
    # ========== 1. 创建用户（固定 Token，方便联调） ==========

    print("\n[1/5] 创建用户...")

    users_data = [
        {"email": "alice@example.com", "token": "sk-seed-alice"},
        {"email": "bob@example.com",   "token": "sk-seed-bob"},
        {"email": "carol@example.com", "token": "sk-seed-carol"},
        {"email": "dave@example.com",  "token": "sk-seed-dave"},
        {"email": "eve@example.com",   "token": "sk-seed-eve"},
    ]

    users = {}
    for u in users_data:
        user = User(
            email=u["email"],
            token=u["token"],
            created_at=datetime.utcnow()
        )
        db.add(user)
        db.flush()
        users[u["email"]] = user
        print(f"  [用户] {u['email']:25s} token={u['token']:20s} id={user.id}")

    db.commit()

    # ========== 2. 会议 1: COLLECTING 状态 ==========

    print("\n[2/5] 创建会议 1: COLLECTING（等待提交）...")

    mtg1 = Meeting(
        id="mtg_seed_001",
        initiator_id=users["alice@example.com"].id,
        title="Sprint 14 规划会议",
        duration_minutes=60,
        status="COLLECTING",
        round_count=0,
        created_at=datetime.utcnow()
    )
    db.add(mtg1)

    # alice（发起人，已提交）
    db.add(NegotiationLog(
        meeting_id="mtg_seed_001",
        user_id=users["alice@example.com"].id,
        role="initiator",
        latest_slots=["2026-03-20 09:00-12:00", "2026-03-20 14:00-17:00"],
        preference_note="上午优先，下午也可以",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # bob（待提交）
    db.add(NegotiationLog(
        meeting_id="mtg_seed_001",
        user_id=users["bob@example.com"].id,
        role="participant",
        latest_slots=[],
        preference_note=None,
        action_required=True,
        created_at=datetime.utcnow()
    ))

    # carol（待提交）
    db.add(NegotiationLog(
        meeting_id="mtg_seed_001",
        user_id=users["carol@example.com"].id,
        role="participant",
        latest_slots=[],
        preference_note=None,
        action_required=True,
        created_at=datetime.utcnow()
    ))

    print("  ✓ mtg_seed_001 [COLLECTING] - alice 发起, bob/carol 待提交")

    # ========== 3. 会议 2: ANALYZING 状态 ==========

    print("\n[3/5] 创建会议 2: ANALYZING 无冲突（Agent 应返回 CONFIRMED）...")

    mtg2 = Meeting(
        id="mtg_seed_002",
        initiator_id=users["dave@example.com"].id,
        title="技术方案评审",
        duration_minutes=30,
        status="ANALYZING",
        round_count=0,
        created_at=datetime.utcnow()
    )
    db.add(mtg2)

    # dave（发起人，已提交）
    db.add(NegotiationLog(
        meeting_id="mtg_seed_002",
        user_id=users["dave@example.com"].id,
        role="initiator",
        latest_slots=["2026-03-21 10:00-12:00", "2026-03-21 14:00-16:00"],
        preference_note="尽量安排在上午",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # alice（已提交）
    db.add(NegotiationLog(
        meeting_id="mtg_seed_002",
        user_id=users["alice@example.com"].id,
        role="participant",
        latest_slots=["2026-03-21 10:00-11:00", "2026-03-21 15:00-17:00"],
        preference_note="10点到11点最好",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # eve（已提交）
    db.add(NegotiationLog(
        meeting_id="mtg_seed_002",
        user_id=users["eve@example.com"].id,
        role="participant",
        latest_slots=["2026-03-21 09:00-12:00"],
        preference_note="只有上午有空",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    print("  ✓ mtg_seed_002 [ANALYZING] 无冲突 - dave 发起, alice/eve 已提交")
    print("    → Agent 应返回 CONFIRMED (三人都在 10:00-11:00 有空)")

    # ========== 4. 会议 3: ANALYZING + 有冲突 ==========

    print("\n[4/5] 创建会议 3: ANALYZING（有冲突，Agent 需生成妥协建议）...")

    mtg3_conflict = Meeting(
        id="mtg_seed_003",
        initiator_id=users["alice@example.com"].id,
        title="产品需求对齐会",
        duration_minutes=60,
        status="ANALYZING",
        round_count=0,
        created_at=datetime.utcnow()
    )
    db.add(mtg3_conflict)

    # alice: 只有上午
    db.add(NegotiationLog(
        meeting_id="mtg_seed_003",
        user_id=users["alice@example.com"].id,
        role="initiator",
        latest_slots=["2026-03-21 09:00-12:00"],
        preference_note="只有上午有空，下午有客户拜访",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # bob: 只有下午
    db.add(NegotiationLog(
        meeting_id="mtg_seed_003",
        user_id=users["bob@example.com"].id,
        role="participant",
        latest_slots=["2026-03-21 14:00-18:00"],
        preference_note="上午有课，只能下午参会",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    # carol: 全天可以
    db.add(NegotiationLog(
        meeting_id="mtg_seed_003",
        user_id=users["carol@example.com"].id,
        role="participant",
        latest_slots=["2026-03-21 09:00-12:00", "2026-03-21 14:00-18:00"],
        preference_note="全天都可以，配合大家时间",
        action_required=False,
        created_at=datetime.utcnow()
    ))

    print("  ✓ mtg_seed_003 [ANALYZING] 有冲突 - alice(上午) vs bob(下午) vs carol(全天)")
    print("    → Agent 应返回 NEGOTIATING + counter_proposals")
    print("    → alice 和 bob 时间完全不重叠，需要妥协")

    # ========== 5. 会议 4: CONFIRMED 状态 ==========

    print("\n[5/5] 创建会议 4: CONFIRMED（已完成）...")

    mtg3 = Meeting(
        id="mtg_seed_004",
        initiator_id=users["bob@example.com"].id,
        title="代码 Review 周会",
        duration_minutes=45,
        status="CONFIRMED",
        final_time="2026-03-19 15:00-15:45",
        round_count=0,
        coordinator_reasoning="所有参与者在周三下午 15:00-16:00 均有空，选取 45 分钟作为会议时间。",
        created_at=datetime.utcnow()
    )
    db.add(mtg3)

    for email, role in [
        ("bob@example.com", "initiator"),
        ("alice@example.com", "participant"),
        ("dave@example.com", "participant")
    ]:
        db.add(NegotiationLog(
            meeting_id="mtg_seed_004",
            user_id=users[email].id,
            role=role,
            latest_slots=["2026-03-19 14:00-17:00"],
            preference_note=None,
            action_required=False,
            created_at=datetime.utcnow()
        ))

    print("  ✓ mtg_seed_004 [CONFIRMED] - bob 发起, final_time=2026-03-19 15:00-15:45")

    db.commit()

    # ========== 输出汇总 ==========

    print(f"\n{'=' * 60}")
    print("  种子数据创建完成！")
    print(f"{'=' * 60}")

    print("\n  用户 Token（固定值，可直接复制使用）：")
    print("  ┌──────────────────────────┬────────────────────┬────┐")
    print("  │ 邮箱                     │ Token              │ ID │")
    print("  ├──────────────────────────┼────────────────────┼────┤")
    for email, user in users.items():
        print(f"  │ {email:24s} │ {user.token:18s} │ {user.id:<2d} │")
    print("  └──────────────────────────┴────────────────────┴────┘")

    print()
    print("  会议列表：")
    print("  ┌──────────────────┬────────────┬──────────────────────┬─────────────────────────────┐")
    print("  │ 会议 ID          │ 状态       │ 标题                 │ 测试用途                    │")
    print("  ├──────────────────┼────────────┼──────────────────────┼─────────────────────────────┤")
    print("  │ mtg_seed_001     │ COLLECTING │ Sprint 14 规划会议   │ Plugin: bob/carol 提交时间  │")
    print("  │ mtg_seed_002     │ ANALYZING  │ 技术方案评审         │ Agent: 无冲突 → CONFIRMED   │")
    print("  │ mtg_seed_003     │ ANALYZING  │ 产品需求对齐会       │ Agent: 有冲突 → NEGOTIATING │")
    print("  │ mtg_seed_004     │ CONFIRMED  │ 代码 Review 周会     │ 查询已完成会议              │")
    print("  └──────────────────┴────────────┴──────────────────────┴─────────────────────────────┘")

    print()
    print("  ━━━━━━━━━━ Agent 团队测试步骤 ━━━━━━━━━━")
    print()
    print("  步骤 1: 轮询待协调任务")
    print("    GET /api/agent/tasks/pending")
    print("    → 应返回 mtg_seed_002 和 mtg_seed_003 两个任务")
    print()
    print("  步骤 2: 对无冲突会议提交 CONFIRMED")
    print("    POST /api/agent/meetings/mtg_seed_002/result")
    print('    body: {"decision_status":"CONFIRMED",')
    print('           "final_time":"2026-03-21 10:00-10:30",')
    print('           "agent_reasoning":"三人都在10:00-11:00有空",')
    print('           "counter_proposals":[]}')
    print()
    print("  步骤 3: 对有冲突会议提交 NEGOTIATING")
    print("    POST /api/agent/meetings/mtg_seed_003/result")
    print('    body: {"decision_status":"NEGOTIATING",')
    print('           "final_time":null,')
    print('           "agent_reasoning":"alice只有上午，bob只有下午，完全冲突",')
    print('           "counter_proposals":[')
    print('             {"target_email":"alice@example.com","message":"bob只有下午有空，您能否调整到13:00？"},')
    print('             {"target_email":"bob@example.com","message":"alice只有上午有空，您能否调整到12:00？"}')
    print("           ]}")
    print()
    print("  ━━━━━━━━━━ Plugin 团队测试步骤 ━━━━━━━━━━")
    print()
    print("  步骤 1: 用 bob 的 token 查询待办")
    print("    GET /api/tasks/pending?token=sk-seed-bob")
    print("    → 应看到 mtg_seed_001 需要提交时间")
    print()
    print("  步骤 2: bob 提交时间")
    print("    POST /api/meetings/mtg_seed_001/submit?token=sk-seed-bob")
    print('    body: {"response_type":"INITIAL",')
    print('           "available_slots":["2026-03-20 10:00-12:00"],')
    print('           "preference_note":"上午有空"}')
    print()
    print("  步骤 3: carol 提交时间 (触发 ANALYZING)")
    print("    POST /api/meetings/mtg_seed_001/submit?token=sk-seed-carol")
    print('    body: {"response_type":"INITIAL",')
    print('           "available_slots":["2026-03-20 14:00-16:00"]}')
    print()

    # ========== 输出联调指南文件 ==========

    guide_content = f"""# Meeting Coordinator - 联调测试指南

> 由 seed_data.py 自动生成于 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

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

{{
  "decision_status": "CONFIRMED",
  "final_time": "2026-03-21 10:00-10:30",
  "agent_reasoning": "三人都在 10:00-11:00 有空，选取前 30 分钟",
  "counter_proposals": []
}}
```

预期：会议状态变为 CONFIRMED，final_time 被设置。

### 步骤 3: 对有冲突会议提交 NEGOTIATING

mtg_seed_003 中 alice 只有上午有空，bob 只有下午有空，时间完全冲突。

```
POST /api/agent/meetings/mtg_seed_003/result
Content-Type: application/json

{{
  "decision_status": "NEGOTIATING",
  "final_time": null,
  "agent_reasoning": "alice 只有上午有空，bob 只有下午有空，完全没有交集",
  "counter_proposals": [
    {{
      "target_email": "alice@example.com",
      "message": "bob 只有下午才有空，您能否将时间延长到 13:00？"
    }},
    {{
      "target_email": "bob@example.com",
      "message": "alice 只有上午有空，您能否提前到 12:00？"
    }}
  ]
}}
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

{{
  "response_type": "INITIAL",
  "available_slots": ["2026-03-20 10:00-12:00"],
  "preference_note": "上午有空"
}}
```

预期：提交成功，状态仍为 COLLECTING（carol 未提交）。

### 步骤 3: carol 提交（触发 ANALYZING）

```
POST /api/meetings/mtg_seed_001/submit?token=sk-seed-carol
Content-Type: application/json

{{
  "response_type": "INITIAL",
  "available_slots": ["2026-03-20 14:00-16:00"],
  "preference_note": "下午有空"
}}
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
| GET | /api/meetings/{{id}} | 需要 | 查询会议详情 |
| POST | /api/meetings/{{id}}/submit | 需要 | 提交时间/响应 |
| GET | /api/tasks/pending | 需要 | Plugin 待办任务 |
| GET | /api/agent/tasks/pending | 无 | Agent 轮询待协调任务 |
| POST | /api/agent/meetings/{{id}}/result | 无 | Agent 提交协调结果 |

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
"""

    guide_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "INTEGRATION_GUIDE.md")
    with open(guide_path, "w", encoding="utf-8") as f:
        f.write(guide_content)

    print(f"  联调指南已输出到: {guide_path}")
    print("  可直接发送给 Agent/Plugin 团队使用")
    print()

except Exception as e:
    db.rollback()
    print(f"\n[ERROR] 种子数据创建失败: {e}")
    raise
finally:
    db.close()
