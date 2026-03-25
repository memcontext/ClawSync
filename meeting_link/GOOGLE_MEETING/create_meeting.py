"""Google Meet 会议链接生成器 — 运行即生成一个真实可入会的链接"""

import os
import uuid
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import SCOPES, CLIENT_SECRET_FILE, TOKEN_FILE


def get_calendar_service():
    """获取已授权的 Google Calendar 服务实例。"""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(
                    f"未找到 {CLIENT_SECRET_FILE}，请从 Google Cloud Console 下载 OAuth 客户端凭据。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def create_meeting(
    summary="在线会议",
    description="",
    start_time=None,
    duration_minutes=60,
    attendees=None,
):
    """创建带有 Google Meet 链接的日历事件并返回信息。"""
    service = get_calendar_service()

    if start_time is None:
        start_time = datetime.now(timezone.utc) + timedelta(minutes=5)

    end_time = start_time + timedelta(minutes=duration_minutes)

    event_body = {
        "summary": summary,
        "description": description,
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": "Asia/Shanghai",
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": "Asia/Shanghai",
        },
        "conferenceData": {
            "createRequest": {
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }

    if attendees:
        event_body["attendees"] = [{"email": email} for email in attendees]

    event = service.events().insert(
        calendarId="primary",
        body=event_body,
        conferenceDataVersion=1,
    ).execute()

    conference = event.get("conferenceData", {})
    meet_link = conference.get("entryPoints", [{}])[0].get("uri", "")

    return {
        "event_id": event["id"],
        "summary": event["summary"],
        "meet_link": meet_link,
        "start_time": event["start"]["dateTime"],
        "end_time": event["end"]["dateTime"],
        "html_link": event["htmlLink"],
    }


if __name__ == "__main__":
    print("正在创建 Google Meet 会议...")
    result = create_meeting(
        summary="测试会议",
        duration_minutes=30,
    )
    print(f"\n会议标题: {result['summary']}")
    print(f"Meet 链接: {result['meet_link']}")
    print(f"开始时间: {result['start_time']}")
    print(f"结束时间: {result['end_time']}")
    print(f"日历链接: {result['html_link']}")
