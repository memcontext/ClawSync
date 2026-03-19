"""公共依赖：数据库会话 & 用户认证"""

from typing import Optional
from fastapi import Depends, Header, Query, HTTPException
from sqlalchemy.orm import Session

from ..models.database import SessionLocal, User


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(
    authorization: Optional[str] = Header(None, description="生产环境用: Bearer sk-你的token"),
    token: Optional[str] = Query(None, description="Swagger测试用: 直接填 sk-你的token"),
    db: Session = Depends(get_db),
) -> User:
    """
    支持两种认证方式:
    1. Header: Authorization: Bearer sk-xxx（生产环境 / 脚本）
    2. Query:  ?token=sk-xxx（Swagger UI 测试）
    """
    token_value = None

    # 优先从 Header 取
    if authorization and authorization.startswith("Bearer "):
        token_value = authorization.replace("Bearer ", "", 1)
    # 其次从 Query 取
    elif token:
        token_value = token

    if not token_value:
        raise HTTPException(status_code=401, detail="缺少认证信息，请提供 Header(Bearer token) 或 Query(?token=xxx)")

    user = db.query(User).filter(User.token == token_value).first()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    return user
