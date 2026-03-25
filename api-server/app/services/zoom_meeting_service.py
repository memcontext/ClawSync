"""Zoom 会议链接生成服务 — 集成 meeting_link/ZOOM_MEETING 模块"""

import base64
import os
import logging
import requests as http_requests

logger = logging.getLogger(__name__)

# Zoom Server-to-Server OAuth 配置
ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID", "tboELECqQmOzjEsqXoEt9w")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID", "OY_0Nvj5RYqrSVkGsHpukQ")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET", "gtLns7ADZMlzSE9ldPjqHiNeaJ18khHJ")

# 绕过系统代理
NO_PROXY = {"http": None, "https": None}


def _get_access_token() -> str:
    """通过 Server-to-Server OAuth 获取 access token"""
    credentials = base64.b64encode(
        f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}".encode()
    ).decode()

    resp = http_requests.post(
        "https://zoom.us/oauth/token",
        headers={"Authorization": f"Basic {credentials}"},
        params={
            "grant_type": "account_credentials",
            "account_id": ZOOM_ACCOUNT_ID,
        },
        proxies=NO_PROXY,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def create_zoom_meeting(
    title: str,
    duration_minutes: int,
    agenda: str = "",
) -> dict | None:
    """
    创建 Zoom 会议并返回链接信息。
    失败返回 None（不阻塞 CONFIRMED 流程）。

    返回: {"meeting_id", "join_url", "start_url", "passcode"}
    """
    try:
        token = _get_access_token()

        resp = http_requests.post(
            "https://api.zoom.us/v2/users/me/meetings",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "topic": title,
                "type": 2,
                "duration": duration_minutes,
                "agenda": agenda,
                "settings": {
                    "join_before_host": True,
                    "waiting_room": False,
                    "meeting_authentication": False,
                },
            },
            proxies=NO_PROXY,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        result = {
            "meeting_id": str(data["id"]),
            "join_url": data["join_url"],
            "start_url": data["start_url"],
            "passcode": data.get("password", ""),
        }
        logger.info(f"Zoom 会议已创建: {result['join_url']}")
        return result

    except Exception as e:
        logger.error(f"Zoom 会议创建失败: {e}")
        return None
