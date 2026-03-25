# Meeting Link Generator

生成真实可入会的会议链接，支持 Zoom 和 Google Meet 两种方案。

## 项目结构

```
meeting_link/
├── ZOOM_MEETING/          # Zoom 方案（推荐）
│   ├── config.py          # Zoom 凭据配置
│   ├── create_meeting.py  # 会议生成脚本
│   └── requirements.txt
├── GOOGLE_MEETING/        # Google Meet 方案
│   ├── config.py          # Google OAuth 配置
│   ├── create_meeting.py  # 会议生成脚本
│   └── requirements.txt
└── README.md
```

## 方案对比

| | Zoom（推荐） | Google Meet |
|--|-------------|-------------|
| 国内访问 | 直连可用 | 需要梯子 |
| 授权方式 | 填 Key 即用，无需登录 | 首次需浏览器登录授权 |
| 依赖 | requests | google-api-python-client |

---

## Zoom 方案

### 1. 获取凭据

1. 登录 [Zoom Marketplace](https://marketplace.zoom.us/)
2. **Develop** → **Build App** → 选择 **Server-to-Server OAuth**
3. 完成 Information、Scopes（添加 `meeting:write:meeting:admin`）、Activation
4. 记录 **Account ID**、**Client ID**、**Client Secret**

### 2. 配置

编辑 `ZOOM_MEETING/config.py`：

```python
ACCOUNT_ID = "你的_Account_ID"
CLIENT_ID = "你的_Client_ID"
CLIENT_SECRET = "你的_Client_Secret"
```

### 3. 安装 & 运行

```bash
pip install -r ZOOM_MEETING/requirements.txt
cd ZOOM_MEETING
python create_meeting.py
```

### 4. 输出示例

```
会议主题: 测试会议
会议 ID:  84808601534
入会链接: https://us05web.zoom.us/j/84808601534?pwd=xxxxx
主持人链接: https://us05web.zoom.us/s/84808601534?zak=xxxxx
会议密码: R1Vv40
```

### 5. 代码调用

```python
from create_meeting import create_meeting

result = create_meeting(
    topic="项目周会",
    duration=30,
    agenda="讨论 Q2 计划",
)
print(result["join_url"])  # 参会者入会链接
```

---

## Google Meet 方案

### 1. 获取凭据

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建项目，启用 **Google Calendar API**
3. 配置 **OAuth 同意屏幕**（添加测试用户）
4. 创建 **OAuth 客户端 ID**（桌面应用），下载 JSON 文件
5. 将 JSON 文件放到 `GOOGLE_MEETING/` 目录下

### 2. 配置

编辑 `GOOGLE_MEETING/config.py`，将 `CLIENT_SECRET_FILE` 改为你下载的 JSON 文件名：

```python
CLIENT_SECRET_FILE = "你下载的文件名.json"
```

### 3. 安装 & 运行

```bash
pip install -r GOOGLE_MEETING/requirements.txt
cd GOOGLE_MEETING
python create_meeting.py
```

首次运行会弹出浏览器进行 Google 账号授权（仅一次），之后自动使用 token。

### 4. 输出示例

```
会议标题: 测试会议
Meet 链接: https://meet.google.com/xxx-yyyy-zzz
开始时间: 2026-03-25T17:05:00+08:00
结束时间: 2026-03-25T17:35:00+08:00
```

### 5. 代码调用

```python
from create_meeting import create_meeting

result = create_meeting(
    summary="项目周会",
    duration_minutes=30,
    attendees=["colleague@gmail.com"],
)
print(result["meet_link"])  # Google Meet 入会链接
```
