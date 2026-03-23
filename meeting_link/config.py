"""Google Calendar API 配置"""

# OAuth 2.0 权限范围
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# OAuth 客户端凭据文件路径（从 Google Cloud Console 下载）
CLIENT_SECRET_FILE = "credentials.json"

# Token 持久化路径
TOKEN_FILE = "token.json"
