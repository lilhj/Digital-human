"""种子数据：管理员/主管/客服账号（Phase 3 C-2 决策：MVP 内置账号）。

用法: cd backend && python -m scripts.seed
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.core.security import ROLE_ADMIN, ROLE_CSR, ROLE_MANAGER, hash_password
from app.domain.models import User

SEED_USERS = [
    {"username": "admin", "password": "admin123", "role": ROLE_ADMIN, "display_name": "系统管理员"},
    {"username": "manager", "password": "manager123", "role": ROLE_MANAGER, "display_name": "客服主管"},
    {"username": "csr", "password": "csr123", "role": ROLE_CSR, "display_name": "客服小一"},
    {"username": "csr2", "password": "csr123", "role": ROLE_CSR, "display_name": "客服小二"},
]


def main() -> None:
    db = SessionLocal()
    created = 0
    try:
        for u in SEED_USERS:
            if db.query(User).filter_by(username=u["username"]).first() is None:
                db.add(
                    User(
                        username=u["username"],
                        password_hash=hash_password(u["password"]),
                        role=u["role"],
                        display_name=u["display_name"],
                    )
                )
                created += 1
        db.commit()
    finally:
        db.close()
    print(f"种子数据完成：新建 {created} 个账号；admin/manager123、manager/manager123、csr/csr123")


if __name__ == "__main__":
    main()
