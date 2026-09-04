"""认证接口：登录签发 JWT。"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import LoginRequest, LoginResponse
from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.domain.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Annotated[Session, Depends(get_db)]):
    user = db.query(User).filter_by(username=body.username, is_active=True).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=401, detail={"code": "BAD_CREDENTIALS", "message": "用户名或密码错误"}
        )
    return LoginResponse(
        access_token=create_access_token(user),
        role=user.role,
        display_name=user.display_name,
    )
