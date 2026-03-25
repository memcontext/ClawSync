import requests
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# 确保从 api-server/.env 加载，无论工作目录在哪
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

logger = logging.getLogger(__name__)

LOOPS_API_KEY = os.getenv("LOOPS_API_KEY", "")
LOOPS_TRANSACTIONAL_ID = os.getenv("LOOPS_TRANSACTIONAL_ID", "")
LOOPS_MEETING_CONFIRMED_ID = os.getenv("LOOPS_MEETING_CONFIRMED_ID", "")

# 启动时打印配置确认
print(f"[Loops] api_key={'***' if LOOPS_API_KEY else 'EMPTY!'}, transactional_id={LOOPS_TRANSACTIONAL_ID or 'EMPTY!'}")


def send_verification_email(to_email: str, code: str) -> bool:
    """通过 Loops.so Transactional API 发送验证码邮件"""
    try:
        resp = requests.post(
            "https://app.loops.so/api/v1/transactional",
            headers={
                "Authorization": f"Bearer {LOOPS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "transactionalId": LOOPS_TRANSACTIONAL_ID,
                "email": to_email,
                "dataVariables": {"code": code},
            },
            timeout=10,
        )
        if resp.status_code == 200 and resp.json().get("success"):
            logger.info(f"验证码邮件已发送至 {to_email}")
            return True
        else:
            logger.error(f"Loops API 错误 ({to_email}): {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"发送邮件失败 ({to_email}): {e}")
        return False


def send_meeting_confirmed_email(
    to_email: str,
    meeting_title: str,
    final_time: str,
    duration_minutes: int,
    meeting_link: str | None,
    initiator_email: str,
) -> bool:
    """会议确认后，发送正式会议通知邮件给参会人"""
    try:
        resp = requests.post(
            "https://app.loops.so/api/v1/transactional",
            headers={
                "Authorization": f"Bearer {LOOPS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "transactionalId": LOOPS_MEETING_CONFIRMED_ID or LOOPS_TRANSACTIONAL_ID,
                "email": to_email,
                "dataVariables": {
                    # 临时方案：复用验证码模板的 code 字段传递会议信息
                    # 等专用模板创建后替换为独立变量
                    "code": (
                        f"会议已确认！\n"
                        f"会议：{meeting_title}\n"
                        f"时间：{final_time}\n"
                        f"时长：{duration_minutes} 分钟\n"
                        f"发起人：{initiator_email}\n"
                        f"会议链接：{meeting_link or '待定'}"
                    ),
                    "meetingTitle": meeting_title,
                    "finalTime": final_time,
                    "durationMinutes": str(duration_minutes),
                    "meetingLink": meeting_link or "待定",
                    "initiatorEmail": initiator_email,
                },
            },
            timeout=10,
        )
        ok = resp.status_code == 200 and resp.json().get("success")
        if ok:
            logger.info(f"会议确认邮件已发送至 {to_email}")
        else:
            logger.error(f"会议确认邮件发送失败 ({to_email}): {resp.text}")
        return ok
    except Exception as e:
        logger.error(f"会议确认邮件异常 ({to_email}): {e}")
        return False
