"""FastAPI 接口 — 生成 Google Meet 会议链接"""

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from google_meet import create_meeting

app = FastAPI(title="Google Meet 会议链接生成器")


class MeetingRequest(BaseModel):
    summary: str = Field(default="在线会议", description="会议标题")
    description: str = Field(default="", description="会议描述")
    start_time: datetime | None = Field(default=None, description="开始时间 (ISO 8601)，为空则默认5分钟后")
    duration_minutes: int = Field(default=60, ge=5, le=1440, description="会议时长（分钟）")
    attendees: list[str] = Field(default_factory=list, description="参会者邮箱列表")


class MeetingResponse(BaseModel):
    event_id: str
    summary: str
    meet_link: str
    start_time: str
    end_time: str
    html_link: str


@app.post("/create-meeting", response_model=MeetingResponse)
def api_create_meeting(req: MeetingRequest):
    """创建 Google Meet 会议并返回入会链接"""
    try:
        result = create_meeting(
            summary=req.summary,
            description=req.description,
            start_time=req.start_time,
            duration_minutes=req.duration_minutes,
            attendees=req.attendees if req.attendees else None,
        )
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建会议失败: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
