#!/usr/bin/env python3
"""
ClawSync Agent 配置文件
集中管理所有 API Key 及外部服务配置，方便后期修改。
"""

# ─── LLM 配置（豆包 / 火山方舟） ─────────────────────────────────────────────
LLM_MODEL = "doubao-1-5-pro-32k-250115"
LLM_API_KEY = "c4d34f89-32e8-4c59-ad87-2029e083c307"
LLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
LLM_TEMPERATURE = 0

# ─── 预留：其他可能用到的 API Key ─────────────────────────────────────────────
# OPENAI_API_KEY = ""
# ANTHROPIC_API_KEY = ""
# GOOGLE_API_KEY = ""

# ─── 预留：邮件 / 通知服务 ───────────────────────────────────────────────────
# SMTP_HOST = ""
# SMTP_PORT = 587
# SMTP_USER = ""
# SMTP_PASSWORD = ""

# ─── 预留：数据库 ────────────────────────────────────────────────────────────
# DATABASE_URL = ""
