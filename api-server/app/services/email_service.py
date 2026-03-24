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
