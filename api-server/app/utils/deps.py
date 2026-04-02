"""Common dependencies: database session & user authentication"""

from typing import Optional
from fastapi import Depends, Header, Query, HTTPException
from sqlalchemy.orm import Session

from ..models.database import SessionLocal, User


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(
    authorization: Optional[str] = Header(None, description="Production use: Bearer sk-your-token"),
    token: Optional[str] = Query(None, description="Swagger testing: directly enter sk-your-token"),
    db: Session = Depends(get_db),
) -> User:
    """
    Supports two authentication methods:
    1. Header: Authorization: Bearer sk-xxx (production / scripts)
    2. Query:  ?token=sk-xxx (Swagger UI testing)
    """
    token_value = None

    # Prefer token from Header
    if authorization and authorization.startswith("Bearer "):
        token_value = authorization.replace("Bearer ", "", 1)
    # Fallback to Query parameter
    elif token:
        token_value = token

    if not token_value:
        raise HTTPException(status_code=401, detail="Missing authentication. Please provide Header(Bearer token) or Query(?token=xxx)")

    user = db.query(User).filter(User.token == token_value).first()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    return user
