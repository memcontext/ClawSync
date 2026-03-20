#!/usr/bin/env python3
"""
Meeting Coordinator Agent —— FastAPI 服务 + 后台轮询

启动方式：
    python agent_runner.py

功能：
    1. 后台定时轮询 API 7（GET /api/agent/tasks/pending）获取待协调会议
    2. 对每个 task 调用 coordinate_from_task 进行 LLM 决策
    3. 将结果通过 API 8（POST /api/agent/meetings/{meeting_id}/result）提交回服务器
    4. 提供 FastAPI 接口，可查看 Agent 运行状态和手动触发处理
"""

import asyncio
import json
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI

from config import (
    AGENT_HOST,
    AGENT_PORT,
    AGENT_TOKEN,
    API_BASE_URL,
    POLL_INTERVAL_SECONDS,
)
from utils import coordinate_from_task

# ─── HTTP 客户端 ──────────────────────────────────────────────────────────────


def _build_headers() -> dict:
    """构建请求头，Token 非空时才带 Authorization。"""
    headers = {"Content-Type": "application/json"}
    if AGENT_TOKEN:
        headers["Authorization"] = f"Bearer {AGENT_TOKEN}"
    return headers


_HEADERS = _build_headers()

# Agent 运行状态（供接口查询）
_state = {
    "started_at": None,
    "last_poll": None,
    "total_processed": 0,
    "last_results": [],     # 最近一轮处理结果（最多保留 10 条）
    "polling": False,
}


# ─── 远程 API 调用 ────────────────────────────────────────────────────────────


async def fetch_pending_tasks(client: httpx.AsyncClient) -> list[dict]:
    """调用 API 7：GET /api/agent/tasks/pending"""
    url = f"{API_BASE_URL}/api/agent/tasks/pending"
    resp = await client.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()

    body = resp.json()
    if body.get("code") != 200:
        print(f"  [WARN] API 7 返回非 200：{body.get('message')}")
        return []

    return body.get("data", {}).get("pending_tasks", [])


async def submit_result(
    client: httpx.AsyncClient, meeting_id: str, result: dict
) -> dict:
    """调用 API 8：POST /api/agent/meetings/{meeting_id}/result"""
    url = f"{API_BASE_URL}/api/agent/meetings/{meeting_id}/result"
    resp = await client.post(url, headers=_HEADERS, json=result, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ─── 单个 Task 处理 ──────────────────────────────────────────────────────────


async def process_task(client: httpx.AsyncClient, task: dict) -> dict:
    """处理单个待协调会议：LLM 决策 → 提交结果 → 返回摘要。"""
    meeting_id = task["meeting_id"]
    title = task.get("title", meeting_id)
    print(f"\n{'─'*50}")
    print(f"  处理会议：{title} ({meeting_id})")
    print(f"  时长：{task.get('duration_minutes', '?')} 分钟")
    print(f"  协商轮次：{task.get('round_count', 0)}")
    print(f"{'─'*50}")

    # 打印从 API 7 获取到的原始参与者数据
    print(f"\n  [收到的数据] participants_data:")
    for p in task.get("participants_data", []):
        role_tag = "发起人" if p.get("role") == "initiator" else "参与者"
        email = p.get("email", f"user_{p.get('user_id')}")
        slots = p.get("latest_slots") or []
        note = (p.get("preference_note") or "").strip()
        print(f"    [{role_tag}] {email}")
        print(f"      latest_slots   : {json.dumps(slots, ensure_ascii=False)}")
        if note:
            print(f"      preference_note: {note}")

    # coordinate_from_task 是同步的（含 LLM 调用），放到线程池执行避免阻塞事件循环
    result = await asyncio.to_thread(coordinate_from_task, task)

    # 打印 Agent 决策结果
    status = result.get("decision_status", "UNKNOWN")
    print(f"\n  [Agent 输出]")
    print(f"    decision_status : {status}")
    if result.get("final_time"):
        print(f"    final_time      : {result['final_time']}")
    print(f"    agent_reasoning : {result.get('agent_reasoning')}")
    print(f"    counter_proposals: {result.get('counter_proposals', [])}")

    # 提交到 API 8
    summary = {
        "meeting_id": meeting_id,
        "title": title,
        "decision_status": status,
        "final_time": result.get("final_time"),
        "processed_at": datetime.now().isoformat(),
    }

    print(f"\n  [提交 API 8] POST /api/agent/meetings/{meeting_id}/result")
    print(f"    请求体: {json.dumps(result, ensure_ascii=False)}")
    try:
        resp = await submit_result(client, meeting_id, result)
        print(f"    响应  : {json.dumps(resp, ensure_ascii=False)}")
        new_status = resp.get("data", {}).get("new_status", "unknown")
        print(f"  OK 提交成功，服务端状态更新为：{new_status}")
        summary["submit_status"] = "success"
        summary["server_new_status"] = new_status
    except httpx.HTTPError as e:
        print(f"  FAIL 提交失败：{e}")
        summary["submit_status"] = f"failed: {e}"

    return summary


# ─── 轮询逻辑 ────────────────────────────────────────────────────────────────


async def poll_once(client: httpx.AsyncClient) -> list[dict]:
    """执行一次轮询，返回本轮处理结果列表。"""
    tasks = await fetch_pending_tasks(client)
    if not tasks:
        return []

    print(f"\n  获取到 {len(tasks)} 个待处理任务")
    results = []
    for task in tasks:
        try:
            summary = await process_task(client, task)
            results.append(summary)
        except Exception:
            meeting_id = task.get("meeting_id", "unknown")
            print(f"\n  FAIL 处理 {meeting_id} 时出错：")
            traceback.print_exc()
            results.append({
                "meeting_id": meeting_id,
                "decision_status": "ERROR",
                "processed_at": datetime.now().isoformat(),
            })

    return results


async def poll_loop():
    """后台持续轮询。"""
    _state["polling"] = True
    async with httpx.AsyncClient() as client:
        while _state["polling"]:
            try:
                _state["last_poll"] = datetime.now().isoformat()
                results = await poll_once(client)

                if results:
                    _state["total_processed"] += len(results)
                    _state["last_results"] = (results + _state["last_results"])[:10]
                else:
                    print(
                        f"  [{time.strftime('%H:%M:%S')}] "
                        f"无待处理任务，{POLL_INTERVAL_SECONDS}s 后重试..."
                    )

            except httpx.HTTPError as e:
                print(
                    f"  [{time.strftime('%H:%M:%S')}] "
                    f"连接失败：{e}，{POLL_INTERVAL_SECONDS}s 后重试..."
                )
            except Exception:
                print(f"  [{time.strftime('%H:%M:%S')}] 未知错误：")
                traceback.print_exc()

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ─── FastAPI 应用 ────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时开始轮询，关闭时停止。"""
    _state["started_at"] = datetime.now().isoformat()
    print(f"\n  后台轮询已启动（间隔 {POLL_INTERVAL_SECONDS}s）")
    task = asyncio.create_task(poll_loop())
    yield
    _state["polling"] = False
    task.cancel()
    print("\n  后台轮询已停止")


app = FastAPI(
    title="Meeting Coordinator Agent",
    description="会议时间协调 Agent，自动轮询 API Server 处理待协调会议",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Agent 健康检查。"""
    return {
        "status": "running" if _state["polling"] else "stopped",
        "started_at": _state["started_at"],
        "last_poll": _state["last_poll"],
        "total_processed": _state["total_processed"],
        "api_server": API_BASE_URL,
        "poll_interval": POLL_INTERVAL_SECONDS,
    }


@app.get("/status")
async def status():
    """Agent 运行状态 + 最近处理记录。"""
    return {
        "status": "running" if _state["polling"] else "stopped",
        "total_processed": _state["total_processed"],
        "last_results": _state["last_results"],
    }


@app.post("/trigger")
async def trigger():
    """手动触发一次轮询（不等待定时器）。"""
    async with httpx.AsyncClient() as client:
        results = await poll_once(client)
        _state["total_processed"] += len(results)
        if results:
            _state["last_results"] = (results + _state["last_results"])[:10]
    return {
        "message": f"手动触发完成，处理了 {len(results)} 个任务",
        "results": results,
    }


# ─── 入口 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  Meeting Coordinator Agent")
    print(f"  API Server : {API_BASE_URL}")
    print(f"  Agent 端口 : {AGENT_PORT}")
    print(f"  轮询间隔   : {POLL_INTERVAL_SECONDS}s")
    print(f"  认证 Token : {'已配置' if AGENT_TOKEN else '未配置（不带 Authorization）'}")
    print("=" * 55)

    uvicorn.run(app, host=AGENT_HOST, port=AGENT_PORT)
