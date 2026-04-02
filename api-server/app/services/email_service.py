import requests
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Ensure loading from api-server/.env regardless of working directory
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

logger = logging.getLogger(__name__)

LOOPS_API_KEY = os.getenv("LOOPS_API_KEY", "")
LOOPS_TRANSACTIONAL_ID = os.getenv("LOOPS_TRANSACTIONAL_ID", "")
LOOPS_MEETING_CONFIRMED_ID = os.getenv("LOOPS_MEETING_CONFIRMED_ID", "")

# Print configuration confirmation on startup
print(f"[Loops] api_key={'***' if LOOPS_API_KEY else 'EMPTY!'}, transactional_id={LOOPS_TRANSACTIONAL_ID or 'EMPTY!'}")


def send_verification_email(to_email: str, code: str) -> bool:
    """Send verification code email via Loops.so Transactional API"""
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
            logger.info(f"Verification email sent to {to_email}")
            return True
        else:
            logger.error(f"Loops API error ({to_email}): {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Failed to send email ({to_email}): {e}")
        return False


def send_meeting_confirmed_email(
    to_email: str,
    meeting_title: str,
    final_time: str,
    duration_minutes: int,
    meeting_link: str | None,
    initiator_email: str,
) -> bool:
    """Send official meeting notification email to participants after meeting is confirmed"""
    try:
        resp = requests.post(
            "https://app.loops.so/api/v1/transactional",
            headers={
                "Authorization": f"Bearer {LOOPS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "transactionalId": LOOPS_MEETING_CONFIRMED_ID,
                "email": to_email,
                "dataVariables": {
                    "meetingTitle": meeting_title,
                    "finalTime": final_time,
                    "durationMinutes": str(duration_minutes),
                    "meetingLink": meeting_link or "TBD",
                    "initiatorEmail": initiator_email,
                },
            },
            timeout=10,
        )
        ok = resp.status_code == 200 and resp.json().get("success")
        if ok:
            logger.info(f"Meeting confirmation email sent to {to_email}")
        else:
            logger.error(f"Failed to send meeting confirmation email ({to_email}): {resp.text}")
        return ok
    except Exception as e:
        logger.error(f"Meeting confirmation email error ({to_email}): {e}")
        return False
