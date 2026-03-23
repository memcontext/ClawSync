# Google Meet 会议链接生成器

基于 Google Calendar API + FastAPI，通过接口创建真实可入会的 Google Meet 链接。

## 项目结构

```
meeting_link/
├── config.py           # 配置（OAuth 范围、文件路径）
├── google_meet.py      # 核心逻辑：OAuth 授权 + 创建带 Meet 链接的日历事件
├── app.py              # FastAPI 接口
├── requirements.txt    # Python 依赖
└── README.md
```

## 使用前准备

### 1. 创建 Google OAuth 凭据

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建项目（或选择已有项目）
3. 在左侧菜单进入 **API 和服务 → 库**，搜索并启用 **Google Calendar API**
4. 进入 **API 和服务 → 凭据**，点击 **创建凭据 → OAuth 客户端 ID**
   - 应用类型选择 **桌面应用**
   - 创建完成后下载 JSON 文件
5. 将下载的 JSON 文件重命名为 `credentials.json`，放到项目根目录

> 如果提示需要配置 OAuth 同意屏幕，先完成同意屏幕配置（测试阶段选"外部"，添加自己的 Google 账号为测试用户即可）。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动服务

```bash
python app.py
```

首次运行会自动弹出浏览器，登录 Google 账号完成授权。授权成功后 token 保存到 `token.json`，后续启动不再弹窗。

服务默认运行在 `http://localhost:8000`，接口文档访问 `http://localhost:8000/docs`。

## 接口说明

### POST /create-meeting

创建 Google Meet 会议并返回入会链接。

**请求体：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| summary | string | 否 | "在线会议" | 会议标题 |
| description | string | 否 | "" | 会议描述 |
| start_time | string | 否 | 当前时间+5分钟 | 开始时间（ISO 8601 格式） |
| duration_minutes | int | 否 | 60 | 会议时长（5-1440 分钟） |
| attendees | string[] | 否 | [] | 参会者邮箱列表 |

**请求示例：**

```bash
curl -X POST http://localhost:8000/create-meeting \
  -H "Content-Type: application/json" \
  -d '{
    "summary": "项目周会",
    "duration_minutes": 30,
    "attendees": ["colleague@gmail.com"]
  }'
```

**返回示例：**

```json
{
  "event_id": "abc123",
  "summary": "项目周会",
  "meet_link": "https://meet.google.com/xxx-yyyy-zzz",
  "start_time": "2026-03-23T17:00:00+08:00",
  "end_time": "2026-03-23T17:30:00+08:00",
  "html_link": "https://calendar.google.com/event?eid=..."
}
```

返回的 `meet_link` 即为真实的 Google Meet 入会链接，任何人点击可直接加入会议。
