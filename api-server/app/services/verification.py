import random
import time
import logging

logger = logging.getLogger(__name__)

CODE_TTL = 300        # Verification code validity: 5 minutes
SEND_INTERVAL = 60    # Send interval: 60 seconds

# In-memory cache: { email: { "code": str, "expires_at": float, "sent_at": float } }
_store: dict[str, dict] = {}


def _cleanup():
    """Lazy cleanup of expired entries"""
    now = time.time()
    expired = [k for k, v in _store.items() if v["expires_at"] < now]
    for k in expired:
        del _store[k]


def can_send(email: str) -> tuple[bool, str]:
    """Check if verification code can be sent, returns (allowed, reason)"""
    _cleanup()
    entry = _store.get(email)
    if entry:
        elapsed = time.time() - entry["sent_at"]
        if elapsed < SEND_INTERVAL:
            remaining = int(SEND_INTERVAL - elapsed)
            return False, f"Please try again in {remaining} seconds"
    return True, ""


def generate_code(email: str) -> str:
    """Generate 6-digit verification code and store in cache"""
    code = f"{random.randint(0, 999999):06d}"
    now = time.time()
    _store[email] = {
        "code": code,
        "expires_at": now + CODE_TTL,
        "sent_at": now,
    }
    logger.info(f"Verification code generated ({email})")
    return code


def verify_code(email: str, code: str) -> tuple[bool, str]:
    """Verify code, delete on success (one-time use), returns (passed, reason)"""
    _cleanup()
    entry = _store.get(email)
    if not entry:
        return False, "Verification code does not exist or has expired. Please request a new one"
    if time.time() > entry["expires_at"]:
        del _store[email]
        return False, "Verification code has expired. Please request a new one"
    if entry["code"] != code:
        return False, "Incorrect verification code"
    del _store[email]
    return True, ""
