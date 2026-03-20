# ClawSync Coordinator Agent

会议时间协调 Agent —— ClawSync 系统的核心决策组件。

Agent 通过轮询 API Server 获取待分析的会议任务，基于所有参与者的时间数据进行冲突分析和最优时间推荐，将决策结果提交回服务端，驱动会议协商流程。

## 系统架构

```
┌──────────────┐     API 7: GET /api/agent/tasks/pending
│  API Server  │ ◄─────────────────────────────────────── Agent 轮询拉取任务
│              │
│  (会议管理    │     API 8: POST /api/agent/meetings/{id}/result
│   用户管理    │ ◄─────────────────────────────────────── Agent 提交决策结果
│   状态流转)   │
└──────┬───────┘
       │  通知用户
       ▼
┌──────────────┐
│ OpenClaw     │  用户通过插件提交/查看时间
│ Plugin       │
└──────────────┘
```

## 决策流程

```
接收任务 (API 7)
    │
    ├─ round_count >= max_rounds？ ──── 是 → 返回 FAILED
    │
    ├─ 解析每位参与者的时间数据
    │   ├─ latest_slots 非空 → 标准格式直接解析
    │   └─ 仅 preference_note → LLM 自然语言解析
    │
    ├─ 打分：统计每个时间槽的可用人数和冲突用户
    │
    ├─ 查找满足 duration_minutes 的连续时间块
    │   │
    │   ├─ 找到全员有空的块 → LLM 生成推荐理由 → CONFIRMED
    │   │
    │   └─ 未找到 → 选冲突最少的 top-1 块
    │              → LLM 生成冲突分析
    │              → 构建 counter_proposals
    │              → NEGOTIATING
    │
    └─ 提交结果 (API 8)
```

## 快速开始

### 环境准备

```bash
conda create -n agent python=3.11 -y
conda activate agent
pip install -r requirements.txt
```

### 配置

编辑 `config.py`：

```python
# 豆包大模型 API Key
DOUBAO_API_KEY = "your-api-key"

# API Server 地址
API_BASE_URL = "http://39.105.143.2:7010"

# 轮询间隔（秒）
POLL_INTERVAL_SECONDS = 5
```

### 运行

```bash
python agent_runner.py
```

启动后 Agent 会：
1. 在 `localhost:8001` 启动 FastAPI 管理服务
2. 每 5 秒轮询 API Server 获取待处理任务
3. 自动处理任务并提交结果

### 管理接口

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /status` | 运行状态 + 最近处理结果 |
| `POST /trigger` | 手动触发一次轮询 |

## 项目结构

```
agent/
├── agent_runner.py            # FastAPI 服务 + 后台轮询
├── config.py                  # 全局配置（API Key、模型、服务地址）
├── requirements.txt           # Python 依赖
├── test.py                    # 测试用例
├── logs/                      # 日志文件（按天分割）
│   └── agent_YYYY-MM-DD.log
├── meeting_time_data/         # 每个会议的参与者时间数据
│   └── {meeting_id}.json
├── meeting_score/             # 每个会议的打分结果
│   └── {meeting_id}.json
└── utils/
    ├── __init__.py            # 统一导出
    ├── logger.py              # 日志模块（控制台 + 文件）
    ├── agent_input_format.py  # 时间解析（标准格式 + LLM 自然语言）
    ├── scoring.py             # 打分逻辑
    ├── input_handle.py        # 流程编排（coordinate_from_task 主入口）
    └── output_summary.py      # 决策分析（CONFIRMED / NEGOTIATING / FAILED）
```

## API 对接

### 输入：API 7 任务格式

```json
{
    "meeting_id": "mtg_xxx",
    "title": "技术方案评审",
    "duration_minutes": 30,
    "round_count": 0,
    "max_rounds": 3,
    "previous_reasoning": null,
    "participants_data": [
        {
            "user_id": 1,
            "email": "alice@example.com",
            "role": "initiator",
            "latest_slots": [
                {"start": "2026-03-21 10:00", "end": "2026-03-21 12:00"}
            ],
            "preference_note": "尽量安排在上午"
        }
    ]
}
```

### 输出：API 8 决策结果

**CONFIRMED**（找到合适时间）：
```json
{
    "decision_status": "CONFIRMED",
    "final_time": "2026-03-21 10:00-10:30",
    "agent_reasoning": "发起人 alice 在 10:00-12:00 有空，参与者 bob 在 10:00-11:00 有空，全员无冲突。",
    "counter_proposals": []
}
```

**NEGOTIATING**（存在冲突）：
```json
{
    "decision_status": "NEGOTIATING",
    "final_time": null,
    "agent_reasoning": "发起人 alice 的可用时间为 09:00-12:00，bob 只有下午有空，与发起人冲突。",
    "counter_proposals": [
        {
            "target_email": "bob@example.com",
            "message": "以下是经过评估得到的需要你进行协调的时间：",
            "suggested_slots": ["2026-03-21 10:00-11:00"]
        }
    ]
}
```

**FAILED**（协商轮次耗尽）：
```json
{
    "decision_status": "FAILED",
    "final_time": null,
    "agent_reasoning": "经过 3 轮协商，参与者依然无法达成一致的会议时间。",
    "counter_proposals": []
}
```

## 核心设计

### 时间槽格式

`YYYY-MM-DD HH:MM--YYYY-MM-DD HH:MM`（30 分钟粒度）

只存储用户实际提到的时间槽，未提到的时间默认不可用。

### 打分规则

对所有参与者提到的时间槽逐槽统计：
- `score`：该槽有空的人数
- `conflict`：该槽不可用的用户列表（包括未提到该槽的用户）

### Initiator 优先

最终推荐时间必须在发起人（initiator）的可用范围内，以发起人为基准分析冲突。

### 协商策略

NEGOTIATING 时选取发起人有空但冲突人数最少的 top-1 时间块，冲突数相同时随机选取。只通知冲突用户，不打扰已配合的参与者。

## 日志

日志同时输出到控制台和 `logs/agent_YYYY-MM-DD.log`，覆盖：

- 任务接收（meeting_id、参与者、时间数据）
- 时间解析（标准格式/自然语言、槽数）
- 打分详情（冲突槽、score 分布）
- LLM 调用（输入摘要、原始返回）
- 决策输出（完整 JSON 结果）

日志级别在 `config.py` 中配置，默认 `DEBUG`。

## 技术栈

- Python 3.11
- LangChain + LangChain-OpenAI（LLM 编排）
- Pydantic v2（数据校验）
- FastAPI + Uvicorn（Agent 管理服务）
- HTTPX（HTTP 客户端）
- 豆包 Doubao Pro 32K（通过火山方舟 OpenAI 兼容接口）
