import secrets
import hashlib

def generate_token(email: str) -> str:
    """生成唯一token"""
    random_part = secrets.token_urlsafe(32)
    hash_part = hashlib.sha256(f"{email}{random_part}".encode()).hexdigest()[:16]
    return f"sk-{random_part[:16]}{hash_part}"

def generate_meeting_id() -> str:
    """生成会议ID"""
    random_part = secrets.token_hex(8)
    return f"mtg_{random_part}"