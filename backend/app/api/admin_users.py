"""ADMIN 用户管理（phase12）：员工（users）与买家（customers）账号生命周期管理。

仅 ADMIN 角色可用（require_roles(ROLE_ADMIN)）。
安全约束（与全站"密码不可逆哈希"一致）：
- 任何响应绝不返回 password_hash；
- 不提供"查看明文密码"能力，只提供"重置密码"（管理员设新密码 -> 重新哈希落库）；
- 员工侧禁止停用/改角色自己（防管理员把自己锁在门外 / 降权自杀）。

职责边界：
- 员工：新增 / 改角色 / 启停用 / 重置密码 / 改显示名
- 买家：列表 / 重置密码 / 启停用（停用后登录与鉴权均拦截，见 get_current_customer）
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import (
    AdminActionResponse,
    AdminCustomerOut,
    AdminUserOut,
    CreateUserRequest,
    ResetPasswordRequest,
    UpdateUserRequest,
)
from app.core.database import get_db
from app.core.security import ROLE_ADMIN, hash_password, require_roles
from app.domain.models import Customer, User

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------- 员工（users 表） ----------


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_roles(ROLE_ADMIN))],
):
    """员工列表（不含密码哈希）。"""
    return db.query(User).order_by(User.id).all()


@router.post("/users", response_model=AdminUserOut, status_code=201)
def create_user(
    body: CreateUserRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_roles(ROLE_ADMIN))],
):
    """新增员工账号（密码以 pbkdf2 哈希落库）。"""
    if db.query(User).filter_by(username=body.username).first() is not None:
        raise HTTPException(status_code=409, detail={"code": "USERNAME_TAKEN", "message": "用户名已存在"})
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        display_name=body.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=AdminActionResponse)
def update_user(
    user_id: int,
    body: UpdateUserRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_roles(ROLE_ADMIN))],
):
    """改角色 / 改显示名 / 启停用。禁止操作自己（防锁死门外 / 降权自杀）。"""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "员工不存在"})
    if target.id == admin.id:
        raise HTTPException(
            status_code=400,
            detail={"code": "SELF_OPERATION", "message": "不能修改/停用当前登录的管理员自己"},
        )
    if body.role is not None:
        target.role = body.role
    if body.display_name is not None:
        target.display_name = body.display_name
    if body.is_active is not None:
        target.is_active = body.is_active
    db.commit()
    return AdminActionResponse(message="已更新")


@router.post("/users/{user_id}/reset-password", response_model=AdminActionResponse)
def reset_user_password(
    user_id: int,
    body: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_roles(ROLE_ADMIN))],
):
    """重置员工密码：新密码哈希后覆盖，旧密码立即失效。"""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "USER_NOT_FOUND", "message": "员工不存在"})
    target.password_hash = hash_password(body.new_password)
    db.commit()
    return AdminActionResponse(message="密码已重置")


# ---------- 买家（customers 表） ----------


@router.get("/customers", response_model=list[AdminCustomerOut])
def list_customers(
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_roles(ROLE_ADMIN))],
):
    """买家列表（不含密码哈希）。"""
    return db.query(Customer).order_by(Customer.id.desc()).limit(200).all()


@router.post("/customers/{customer_id}/reset-password", response_model=AdminActionResponse)
def reset_customer_password(
    customer_id: int,
    body: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_roles(ROLE_ADMIN))],
):
    """重置买家密码：新密码哈希后覆盖，旧密码立即失效。"""
    target = db.get(Customer, customer_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "买家不存在"})
    target.password_hash = hash_password(body.new_password)
    db.commit()
    return AdminActionResponse(message="密码已重置")


@router.post("/customers/{customer_id}/toggle-active", response_model=AdminActionResponse)
def toggle_customer_active(
    customer_id: int,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[User, Depends(require_roles(ROLE_ADMIN))],
):
    """启用/停用买家：停用后登录与鉴权均被拦截（ACCOUNT_DISABLED）。"""
    target = db.get(Customer, customer_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "买家不存在"})
    target.is_active = not target.is_active
    db.commit()
    return AdminActionResponse(message="已启用" if target.is_active else "已停用")
