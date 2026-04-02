#!/usr/bin/env python3
"""
Meeting Coordinator Agent -- FastAPI Service + Background Polling

Start command:
    python agent_runner.py

Features:
    1. Background periodic polling of API 7 (GET /api/agent/tasks/pending) to fetch pending meetings
    2. For each task, calls coordinate_from_task for LLM-based decision making
    3. Submits results via API 8 (POST /api/agent/meetings/{meeting_id}/result) back to the server
    4. Provides FastAPI endpoints to view Agent status and manually trigger processing
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

# ─── HTTP Client ─────────────────────────────────────────────────────────────


def _build_headers() -> dict:
    """Build request headers; include Authorization only when Token is non-empty."""
    headers = {"Content-Type": "application/json"}
    if AGENT_TOKEN:
        headers["Authorization"] = f"Bearer {AGENT_TOKEN}"
    return headers


_HEADERS = _build_headers()

# Agent runtime state (for API queries)
_state = {
    "started_at": None,
    "last_poll": None,
    "total_processed": 0,
    "last_results": [],     # Most recent processing results (keep up to 10)
    "polling": False,
}


# ─── Remote API Calls ────────────────────────────────────────────────────────


async def fetch_pending_tasks(client: httpx.AsyncClient) -> list[dict]:
    """Call API 7: GET /api/agent/tasks/pending"""
    url = f"{API_BASE_URL}/api/agent/tasks/pending"
    resp = await client.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()

    body = resp.json()
    if body.get("code") != 200:
        print(f"  [WARN] API 7 returned non-200: {body.get('message')}")
        return []

    return body.get("data", {}).get("pending_tasks", [])


async def submit_result(
    client: httpx.AsyncClient, meeting_id: str, result: dict
) -> dict:
    """Call API 8: POST /api/agent/meetings/{meeting_id}/result"""
    url = f"{API_BASE_URL}/api/agent/meetings/{meeting_id}/result"
    resp = await client.post(url, headers=_HEADERS, json=result, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ─── Single Task Processing ──────────────────────────────────────────────────


async def process_task(client: httpx.AsyncClient, task: dict) -> dict:
    """Process a single pending meeting: LLM decision -> submit result -> return summary."""
    meeting_id = task["meeting_id"]
    title = task.get("title", meeting_id)
    print(f"\n{'─'*50}")
    print(f"  Processing meeting: {title} ({meeting_id})")
    print(f"  Duration: {task.get('duration_minutes', '?')} minutes")
    print(f"  Negotiation round: {task.get('round_count', 0)}")
    print(f"{'─'*50}")

    # Print raw participant data received from API 7
    print(f"\n  [Received data] participants_data:")
    for p in task.get("participants_data", []):
        role_tag = "Initiator" if p.get("role") == "initiator" else "Participant"
        email = p.get("email", f"user_{p.get('user_id')}")
        slots = p.get("latest_slots") or []
        note = (p.get("preference_note") or "").strip()
        print(f"    [{role_tag}] {email}")
        print(f"      latest_slots   : {json.dumps(slots, ensure_ascii=False)}")
        if note:
            print(f"      preference_note: {note}")

    # coordinate_from_task is synchronous (contains LLM calls), run in thread pool to avoid blocking event loop
    result = await asyncio.to_thread(coordinate_from_task, task)

    # Print Agent decision result
    status = result.get("decision_status", "UNKNOWN")
    print(f"\n  [Agent output]")
    print(f"    decision_status : {status}")
    if result.get("final_time"):
        print(f"    final_time      : {result['final_time']}")
    print(f"    agent_reasoning : {result.get('agent_reasoning')}")
    print(f"    counter_proposals: {result.get('counter_proposals', [])}")

    # Submit to API 8
    summary = {
        "meeting_id": meeting_id,
        "title": title,
        "decision_status": status,
        "final_time": result.get("final_time"),
        "processed_at": datetime.now().isoformat(),
    }

    print(f"\n  [Submit API 8] POST /api/agent/meetings/{meeting_id}/result")
    print(f"    Request body: {json.dumps(result, ensure_ascii=False)}")
    try:
        resp = await submit_result(client, meeting_id, result)
        print(f"    Response: {json.dumps(resp, ensure_ascii=False)}")
        new_status = resp.get("data", {}).get("new_status", "unknown")
        print(f"  OK Submission successful, server status updated to: {new_status}")
        summary["submit_status"] = "success"
        summary["server_new_status"] = new_status
    except httpx.HTTPError as e:
        print(f"  FAIL Submission failed: {e}")
        summary["submit_status"] = f"failed: {e}"

    return summary


# ─── Polling Logic ───────────────────────────────────────────────────────────


async def poll_once(client: httpx.AsyncClient) -> list[dict]:
    """Execute one polling cycle, return this round's processing results."""
    tasks = await fetch_pending_tasks(client)
    if not tasks:
        return []

    print(f"\n  Found {len(tasks)} pending tasks")
    results = []
    for task in tasks:
        try:
            summary = await process_task(client, task)
            results.append(summary)
        except Exception:
            meeting_id = task.get("meeting_id", "unknown")
            print(f"\n  FAIL Error processing {meeting_id}:")
            traceback.print_exc()
            results.append({
                "meeting_id": meeting_id,
                "decision_status": "ERROR",
                "processed_at": datetime.now().isoformat(),
            })

    return results


async def poll_loop():
    """Background continuous polling."""
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
                        f"No pending tasks, retrying in {POLL_INTERVAL_SECONDS}s..."
                    )

            except httpx.HTTPError as e:
                print(
                    f"  [{time.strftime('%H:%M:%S')}] "
                    f"Connection failed: {e}, retrying in {POLL_INTERVAL_SECONDS}s..."
                )
            except Exception:
                print(f"  [{time.strftime('%H:%M:%S')}] Unknown error:")
                traceback.print_exc()

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ─── FastAPI Application ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start polling on startup, stop on shutdown."""
    _state["started_at"] = datetime.now().isoformat()
    print(f"\n  Background polling started (interval {POLL_INTERVAL_SECONDS}s)")
    task = asyncio.create_task(poll_loop())
    yield
    _state["polling"] = False
    task.cancel()
    print("\n  Background polling stopped")


app = FastAPI(
    title="Meeting Coordinator Agent",
    description="Meeting time coordination Agent, automatically polls API Server to process pending meetings",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Agent health check."""
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
    """Agent runtime status + recent processing records."""
    return {
        "status": "running" if _state["polling"] else "stopped",
        "total_processed": _state["total_processed"],
        "last_results": _state["last_results"],
    }


@app.post("/trigger")
async def trigger():
    """Manually trigger one polling cycle (without waiting for the timer)."""
    async with httpx.AsyncClient() as client:
        results = await poll_once(client)
        _state["total_processed"] += len(results)
        if results:
            _state["last_results"] = (results + _state["last_results"])[:10]
    return {
        "message": f"Manual trigger completed, processed {len(results)} tasks",
        "results": results,
    }


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  Meeting Coordinator Agent")
    print(f"  API Server : {API_BASE_URL}")
    print(f"  Agent Port : {AGENT_PORT}")
    print(f"  Poll Interval: {POLL_INTERVAL_SECONDS}s")
    print(f"  Auth Token : {'Configured' if AGENT_TOKEN else 'Not configured (no Authorization header)'}")
    print("=" * 55)

    uvicorn.run(app, host=AGENT_HOST, port=AGENT_PORT)
