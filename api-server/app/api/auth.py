from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from ..models.database import User
from ..models.schemas import UserCreate, SendCodeRequest, VerifyBindRequest, APIResponse
from ..utils.token import generate_token
from ..utils.deps import get_db
from ..services.verification import can_send, generate_code, verify_code
from ..services.email_service import send_verification_email

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/send-code", response_model=APIResponse)
def send_code(req: SendCodeRequest):
    """Send verification code to email (synchronous, FastAPI auto-dispatches to thread pool)"""
    ok, reason = can_send(req.email)
    if not ok:
        return APIResponse(code=429, message=reason)

    code = generate_code(req.email)
    success = send_verification_email(req.email, code)
    if not success:
        return APIResponse(code=500, message="Failed to send email, please try again later")

    return APIResponse(code=200, message="Verification code sent, please check your email")


@router.post("/verify-bind", response_model=APIResponse)
async def verify_bind(req: VerifyBindRequest, db: Session = Depends(get_db)):
    """Bind email after verification code is validated"""
    ok, reason = verify_code(req.email, req.code)
    if not ok:
        return APIResponse(code=400, message=reason)

    try:
        user = db.query(User).filter(User.email == req.email).first()

        if user:
            user.email_verified = True
            db.commit()
            return APIResponse(
                code=200,
                message="Verification successful",
                data={"token": user.token, "user_id": user.id}
            )

        new_token = generate_token(req.email)
        new_user = User(
            email=req.email,
            token=new_token,
            email_verified=True,
            created_at=datetime.utcnow()
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return APIResponse(
            code=200,
            message="Verification and registration successful",
            data={"token": new_user.token, "user_id": new_user.id}
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bind", response_model=APIResponse, deprecated=True)
async def bind_email(user_data: UserCreate, db: Session = Depends(get_db)):
    """[Deprecated] Direct email binding (no verification, kept for backward compatibility)"""
    try:
        user = db.query(User).filter(User.email == user_data.email).first()

        if user:
            return APIResponse(
                code=200,
                message="User already exists",
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
            message="Registration successful",
            data={
                "token": new_user.token,
                "user_id": new_user.id
            }
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
