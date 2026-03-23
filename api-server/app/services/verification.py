import random
import time
import logging

logger = logging.getLogger(__name__)

CODE_TTL = 300        # 验证码有效期 5 分钟
SEND_INTERVAL = 60    # 发送间隔 60 秒

# 内存缓存: { email: { "code": str, "expires_at": float, "sent_at": float } }
_store: dict[str, dict] = {}


def _cleanup():
    """惰性清理过期条目"""
    now = time.time()
    expired = [k for k, v in _store.items() if v["expires_at"] < now]
    for k in expired:
        del _store[k]


def can_send(email: str) -> tuple[bool, str]:
    """检查是否可以发送验证码，返回 (可否, 原因)"""
    _cleanup()
    entry = _store.get(email)
    if entry:
        elapsed = time.time() - entry["sent_at"]
        if elapsed < SEND_INTERVAL:
            remaining = int(SEND_INTERVAL - elapsed)
            return False, f"请 {remaining} 秒后再试"
    return True, ""


def generate_code(email: str) -> str:
    """生成 6 位验证码并存入缓存"""
    code = f"{random.randint(0, 999999):06d}"
    now = time.time()
    _store[email] = {
        "code": code,
        "expires_at": now + CODE_TTL,
        "sent_at": now,
    }
    logger.info(f"已生成验证码 ({email})")
    return code


def verify_code(email: str, code: str) -> tuple[bool, str]:
    """校验验证码，成功后删除（一次性），返回 (是否通过, 原因)"""
    _cleanup()
    entry = _store.get(email)
    if not entry:
        return False, "验证码不存在或已过期，请重新获取"
    if time.time() > entry["expires_at"]:
        del _store[email]
        return False, "验证码已过期，请重新获取"
    if entry["code"] != code:
        return False, "验证码错误"
    del _store[email]
    return True, ""
