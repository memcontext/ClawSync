"""Zoom 会议链接生成器 — 使用 Server-to-Server OAuth"""

import base64
import os
import requests

from config import ACCOUNT_ID, CLIENT_ID, CLIENT_SECRET

# 绕过系统代理，直接连接
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
NO_PROXY = {"http": None, "https": None}


def get_access_token():
    """通过 Server-to-Server OAuth 获取 access token。"""
    credentials = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    ).decode()

    resp = requests.post(
        "https://zoom.us/oauth/token",
        headers={"Authorization": f"Basic {credentials}"},
        params={
            "grant_type": "account_credentials",
            "account_id": ACCOUNT_ID,
        },
        proxies=NO_PROXY,
    )
    if resp.status_code != 200:
        print(f"Token 请求失败 [{resp.status_code}]: {resp.text}")
        resp.raise_for_status()
    return resp.json()["access_token"]


def create_meeting(
    topic="在线会议",
    duration=60,
    agenda="",
):
    """创建 Zoom 会议并返回入会链接。

    Args:
        topic: 会议标题
        duration: 会议时长（分钟）
        agenda: 会议描述
    """
    token = get_access_token()

    resp = requests.post(
        "https://api.zoom.us/v2/users/me/meetings",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "topic": topic,
            "type": 2,  # 预定会议
            "duration": duration,
            "agenda": agenda,
            "settings": {
                "join_before_host": True,
                "waiting_room": False,
                "meeting_authentication": False,
            },
        },
        proxies=NO_PROXY,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "meeting_id": data["id"],
        "topic": data["topic"],
        "join_url": data["join_url"],       # 参会者入会链接
        "start_url": data["start_url"],     # 主持人启动链接
        "passcode": data.get("password", ""),
    }


if __name__ == "__main__":
    print("正在创建 Zoom 会议...")
    result = create_meeting(
        topic="测试会议",
        duration=30,
    )
    print(f"\n会议主题: {result['topic']}")
    print(f"会议 ID:  {result['meeting_id']}")
    print(f"入会链接: {result['join_url']}")
    print(f"主持人链接: {result['start_url']}")
    if result["passcode"]:
        print(f"会议密码: {result['passcode']}")
