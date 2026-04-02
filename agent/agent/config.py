#!/usr/bin/env python3
"""
Global configuration: centralized management of API keys, model parameters,
and other configuration items that may need to be modified.
For production, it is recommended to migrate sensitive information to environment variables or secret management services.
"""

# ─── Claude LLM Configuration ────────────────────────────────────────────────
# Accessed via sz.uyilink.com proxy
DOUBAO_API_KEY = "sk-7y2TdJzw6TpQJAsrVYEDKWtQdtfHo20OjKJm8zc8yTgtIRQR"
DOUBAO_BASE_URL = "https://sz.uyilink.com/v1"
DOUBAO_MODEL = "claude-sonnet-4-6"

# ─── LLM Default Parameters ──────────────────────────────────────────────────
LLM_TEMPERATURE = 0

# ─── Date Placeholder (to be replaced with actual date from meeting data) ────
PLACEHOLDER_DATE = "2026-01-01"

# ─── API Server Configuration ────────────────────────────────────────────────
API_BASE_URL = "http://39.105.143.2:7010"
AGENT_TOKEN = ""                              # Leave empty to omit Authorization header

# ─── Polling Configuration ───────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 5

# ─── Negotiation Configuration ───────────────────────────────────────────────
NEGOTIATION_TOP_K = 3                         # Number of candidate time blocks suggested during NEGOTIATING

# ─── Logging Configuration ───────────────────────────────────────────────────
LOG_DIR = "logs"                              # Log file directory
LOG_LEVEL = "DEBUG"                           # Log level: DEBUG / INFO / WARNING / ERROR

# ─── Agent Service Configuration ─────────────────────────────────────────────
AGENT_HOST = "0.0.0.0"
AGENT_PORT = 8001                             # Agent listening port (avoid conflict with API Server)
