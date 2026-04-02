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

# ========== Logging Configuration ==========

# Create log directory
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# API request logger
api_logger = logging.getLogger("api")
api_logger.setLevel(logging.INFO)

# File Handler -- daily log files
api_file_handler = logging.FileHandler(
    os.path.join(LOG_DIR, f"api_{datetime.now().strftime('%Y%m%d')}.log"),
    encoding="utf-8"
)
api_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
api_logger.addHandler(api_file_handler)

# Console Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(message)s",
    datefmt="%H:%M:%S"
))
api_logger.addHandler(console_handler)

# State change logger (separately records meeting state transitions)
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


# ========== Logging Middleware ==========

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log HTTP method, path, status code, duration, and source IP for each request"""

    # Do not log 200 responses for high-frequency polling endpoints (reduce log noise)
    QUIET_PATHS = {"/api/tasks/pending", "/health"}

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        client_ip = request.client.host if request.client else "unknown"
        method = request.method
        path = request.url.path
        query = str(request.query_params) if request.query_params else ""

        # Read request body (POST/PUT only)
        body_summary = ""
        if method in ("POST", "PUT"):
            try:
                body = await request.body()
                if body:
                    import json
                    body_json = json.loads(body)
                    # Hide sensitive fields
                    if "token" in body_json:
                        body_json["token"] = "***"
                    body_summary = json.dumps(body_json, ensure_ascii=False)
                    if len(body_summary) > 200:
                        body_summary = body_summary[:200] + "..."
            except:
                body_summary = "(unable to parse)"

        # Reset request body stream to ensure downstream routes can read body
        if method in ("POST", "PUT"):
            _body = body if 'body' in locals() else b""
            async def receive():
                return {"type": "http.request", "body": _body}
            request._receive = receive

        response = await call_next(request)
        duration = round((time.time() - start_time) * 1000)  # milliseconds

        # Build log message
        log_msg = f"{client_ip} | {method} {path}"
        if query:
            log_msg += f"?{query}"
        log_msg += f" | {response.status_code} | {duration}ms"
        if body_summary:
            log_msg += f" | body={body_summary}"

        # Silently handle normal responses for high-frequency polling
        if path in self.QUIET_PATHS and response.status_code == 200:
            api_logger.debug(log_msg)  # debug level, not shown by default
        else:
            api_logger.info(log_msg)

        # Extra logging for error requests
        if response.status_code >= 400:
            api_logger.warning(f"Abnormal request: {log_msg}")

        return response


# Initialize database
init_db()

# Create FastAPI application
app = FastAPI(
    title="Meeting Coordinator API",
    description="Multi-agent meeting coordination system",
    version="1.0.0",
    swagger_ui_init_oauth={},
)

# Add logging middleware
app.add_middleware(RequestLoggingMiddleware)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== Global Exception Handlers ==========

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Unified HTTP exception format"""
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
    """Unified request validation error format"""
    errors = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error["loc"])
        errors.append(f"{field}: {error['msg']}")

    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "message": "Request parameter validation failed",
            "data": {"errors": errors}
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Catch-all: unhandled exceptions"""
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": f"Internal server error: {str(exc)}",
            "data": None
        }
    )


# ========== Register Routes ==========

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
            "POST /api/auth/bind                      - Email binding/registration",
            "GET  /api/meetings                        - My meeting list",
            "POST /api/meetings                        - Create meeting",
            "GET  /api/meetings/{id}                   - Query meeting details",
            "POST /api/meetings/{id}/submit            - Submit availability/response",
            "GET  /api/tasks/pending                   - Pending tasks (Plugin polling)",
            "GET  /api/agent/tasks/pending              - Agent pending coordination tasks",
            "POST /api/agent/meetings/{id}/result       - Agent submit coordination result"
        ]
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }
