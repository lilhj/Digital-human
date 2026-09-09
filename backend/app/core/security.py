"""安全组件：密码哈希（标准库 PBKDF2）+ JWT 签发与校验 + 鉴权依赖。"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.domain.models import Customer, User

PBKDF2_ITERATIONS = 100_000
bearer_scheme = HTTPBearer(auto_error=False)

# 角色常量
ROLE_CSR = "CSR"
ROLE_MANAGER = "MANAGER"
ROLE_ADMIN = "ADMIN"

# 令牌类型（v2.0 §6.4 C1：JWT 分流 STAFF / BUYER）
TOKEN_TYPE_STAFF = "STAFF"
TOKEN_TYPE_BUYER = "BUYER"


@dataclass
class Principal:
    """统一主体（STAFF 或 BUYER），用于双方都能访问的接口（如 create_case）。"""

    sub: str
    token_type: str


# ---------- 密码哈希（C-4 裁决：标准库 pbkdf2，零第三方依赖） ----------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        ).hex()
        return hmac.compare_digest(calc, digest)
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------

def create_access_token(user: User) -> str:
    settings = get_settings()
    payload = {
        "sub": user.username,
        "role": user.role,
        "type": TOKEN_TYPE_STAFF,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_buyer_token(customer: Customer) -> str:
    """签发买家令牌：sub=手机号，type=BUYER。"""
    settings = get_settings()
    payload = {
        "sub": customer.phone,
        "type": TOKEN_TYPE_BUYER,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail={"code": "TOKEN_EXPIRED", "message": "登录已过期"}) from e
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail={"code": "INVALID_TOKEN", "message": "无效的令牌"}) from e


# ---------- 鉴权依赖 ----------

def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if creds is None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "未登录"})
    payload = decode_token(creds.credentials)
    if payload.get("type") == TOKEN_TYPE_BUYER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "买家令牌不可访问员工接口"},
        )
    user = db.query(User).filter_by(username=payload["sub"], is_active=True).first()
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "USER_NOT_FOUND", "message": "用户不存在"})
    return user


def get_current_customer(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Customer:
    """买家依赖：令牌必须为 BUYER 类型。"""
    if creds is None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "未登录"})
    payload = decode_token(creds.credentials)
    if payload.get("type") != TOKEN_TYPE_BUYER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "需买家登录"},
        )
    customer = db.query(Customer).filter_by(phone=payload["sub"]).first()
    if customer is None:
        raise HTTPException(status_code=401, detail={"code": "USER_NOT_FOUND", "message": "买家不存在"})
    if not customer.is_active:
        raise HTTPException(status_code=403, detail={"code": "ACCOUNT_DISABLED", "message": "账号已被停用"})
    return customer


def get_current_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Principal:
    """统一主体依赖：STAFF 与 BUYER 均可（R2 放开 create_case 鉴权）。"""
    if creds is None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "未登录"})
    payload = decode_token(creds.credentials)
    token_type = payload.get("type", TOKEN_TYPE_STAFF)
    if token_type == TOKEN_TYPE_BUYER:
        customer = db.query(Customer).filter_by(phone=payload["sub"]).first()
        if customer is None:
            raise HTTPException(status_code=401, detail={"code": "USER_NOT_FOUND", "message": "买家不存在"})
        return Principal(sub=customer.phone, token_type=TOKEN_TYPE_BUYER)
    user = db.query(User).filter_by(username=payload["sub"], is_active=True).first()
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "USER_NOT_FOUND", "message": "用户不存在"})
    return Principal(sub=user.username, token_type=TOKEN_TYPE_STAFF)


def require_roles(*roles: str):
    """角色权限依赖：必须是指定角色之一，否则 403。"""

    def checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": "权限不足"},
            )
        return user

    return checker
