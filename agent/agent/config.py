#!/usr/bin/env python3
"""
全局配置：集中管理 API Key、模型参数等后续可能需要修改的配置项。
生产环境建议将敏感信息迁移至环境变量或密钥管理服务。
"""

# ─── Claude 大模型配置 ────────────────────────────────────────────────────────
# 通过 OpenAI 兼容接口接入
DOUBAO_API_KEY = "sk-7y2TdJzw6TpQJAsrVYEDKWtQdtfHo20OjKJm8zc8yTgtIRQR"
DOUBAO_BASE_URL = "https://sz.uyilink.com/v1"
DOUBAO_MODEL = "claude-opus-4-6"

# ─── LLM 默认参数 ────────────────────────────────────────────────────────────
LLM_TEMPERATURE = 0

# ─── 日期占位符（后续改为从会议数据中读取真实日期）─────────────────────────────
PLACEHOLDER_DATE = "2026-01-01"

# ─── API Server 配置 ──────────────────────────────────────────────────────────
API_BASE_URL = "http://39.105.143.2:7010"
AGENT_TOKEN = ""                              # 留空则不带 Authorization 头

# ─── 轮询配置 ─────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 5

# ─── 协商配置 ─────────────────────────────────────────────────────────────────
NEGOTIATION_TOP_K = 3                         # NEGOTIATING 时建议的候选时间块数量

# ─── 日志配置 ─────────────────────────────────────────────────────────────────
LOG_DIR = "logs"                              # 日志文件目录
LOG_LEVEL = "DEBUG"                           # 日志级别：DEBUG / INFO / WARNING / ERROR

# ─── Agent 自身服务配置 ──────────────────────────────────────────────────────
AGENT_HOST = "0.0.0.0"
AGENT_PORT = 8001                             # Agent 自身监听端口（避免与 API Server 冲突）
