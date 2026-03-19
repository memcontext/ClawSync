from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from ..models.database import User
from ..models.schemas import UserCreate, APIResponse
from ..utils.token import generate_token
from ..utils.deps import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/bind", response_model=APIResponse)
async def bind_email(user_data: UserCreate, db: Session = Depends(get_db)):
    try:
        user = db.query(User).filter(User.email == user_data.email).first()

        if user:
            return APIResponse(
                code=200,
                message="用户已存在",
                data={
                    "token": user.token,
                    "user_id": user.id
                }
            )

        new_token = generate_token(user_data.email)
        new_user = User(
            email=user_data.email,
            token=new_token,
            created_at=datetime.utcnow()
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return APIResponse(
            code=200,
            message="注册成功",
            data={
                "token": new_user.token,
                "user_id": new_user.id
            }
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))