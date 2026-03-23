from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime
import logging
import time
import os

from .api import auth, meetings, tasks, agent
from .models.database import init_db

# ========== 日志配置 ==========

# 创建日志目录
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# API 请求日志
api_logger = logging.getLogger("api")
api_logger.setLevel(logging.INFO)

# 文件 Handler — 按天记录
api_file_handler = logging.FileHandler(
    os.path.join(LOG_DIR, f"api_{datetime.now().strftime('%Y%m%d')}.log"),
    encoding="utf-8"
)
api_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
api_logger.addHandler(api_file_handler)

# 控制台 Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(message)s",
    datefmt="%H:%M:%S"
))
api_logger.addHandler(console_handler)

# 状态变更日志（单独记录会议状态流转）
state_logger = logging.getLogger("state")
state_logger.setLevel(logging.INFO)
state_file_handler = logging.FileHandler(
    os.path.join(LOG_DIR, "state_transitions.log"),
    encoding="utf-8"
)
state_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
state_logger.addHandler(state_file_handler)


# ========== 日志中间件 ==========

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录每个 HTTP 请求的方法、路径、状态码、耗时、来源 IP"""

    # 不记录高频轮询接口的 200 响应（减少日志噪音）
    QUIET_PATHS = {"/api/tasks/pending", "/health"}

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        client_ip = request.client.host if request.client else "unknown"
        method = request.method
        path = request.url.path
        query = str(request.query_params) if request.query_params else ""

        # 读取请求体（仅 POST/PUT）
        body_summary = ""
        if method in ("POST", "PUT"):
            try:
                body = await request.body()
                if body:
                    import json
                    body_json = json.loads(body)
                    # 隐藏敏感字段
                    if "token" in body_json:
                        body_json["token"] = "***"
                    body_summary = json.dumps(body_json, ensure_ascii=False)
                    if len(body_summary) > 200:
                        body_summary = body_summary[:200] + "..."
            except:
                body_summary = "(无法解析)"

        # 重置请求体流，确保后续路由能读取到 body
        if method in ("POST", "PUT"):
            _body = body if 'body' in locals() else b""
            async def receive():
                return {"type": "http.request", "body": _body}
            request._receive = receive

        response = await call_next(request)
        duration = round((time.time() - start_time) * 1000)  # 毫秒

        # 构建日志消息
        log_msg = f"{client_ip} | {method} {path}"
        if query:
            log_msg += f"?{query}"
        log_msg += f" | {response.status_code} | {duration}ms"
        if body_summary:
            log_msg += f" | body={body_summary}"

        # 静默处理高频轮询的正常响应
        if path in self.QUIET_PATHS and response.status_code == 200:
            api_logger.debug(log_msg)  # debug 级别，默认不显示
        else:
            api_logger.info(log_msg)

        # 错误请求额外记录
        if response.status_code >= 400:
            api_logger.warning(f"⚠️ 异常请求: {log_msg}")

        return response


# 初始化数据库
init_db()

# 创建FastAPI应用
app = FastAPI(
    title="Meeting Coordinator API",
    description="多智能体会议协调系统",
    version="1.0.0",
    swagger_ui_init_oauth={},
)

# 添加日志中间件
app.add_middleware(RequestLoggingMiddleware)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 全局异常处理 ==========

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """统一 HTTP 异常格式"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": str(exc.detail),
            "data": None
        }
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """统一请求校验错误格式"""
    errors = []
    for error in exc.errors():
        field = " → ".join(str(loc) for loc in error["loc"])
        errors.append(f"{field}: {error['msg']}")

    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "message": "请求参数校验失败",
            "data": {"errors": errors}
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """兜底：未捕获的异常"""
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": f"服务器内部错误: {str(exc)}",
            "data": None
        }
    )


# ========== 注册路由 ==========

app.include_router(auth.router)
app.include_router(meetings.router)
app.include_router(tasks.router)
app.include_router(agent.router)


@app.get("/")
async def root():
    return {
        "message": "Meeting Coordinator API Server",
        "version": "1.0.0",
        "status": "running",
        "endpoints": [
            "POST /api/auth/bind                      - 邮箱绑定/注册",
            "GET  /api/meetings                        - 我的会议列表",
            "POST /api/meetings                        - 创建会议",
            "GET  /api/meetings/{id}                   - 查询会议详情",
            "POST /api/meetings/{id}/submit            - 提交空闲时间/响应",
            "GET  /api/tasks/pending                   - 待办任务（Plugin 轮询）",
            "GET  /api/agent/tasks/pending              - Agent 待协调任务",
            "POST /api/agent/meetings/{id}/result       - Agent 提交协调结果"
        ]
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }
