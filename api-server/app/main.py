from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from datetime import datetime

from .api import auth, meetings, tasks, agent
from .models.database import init_db

# 初始化数据库
init_db()

# 创建FastAPI应用
app = FastAPI(
    title="Meeting Coordinator API",
    description="多智能体会议协调系统",
    version="1.0.0",
    swagger_ui_init_oauth={},
)


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
