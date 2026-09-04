"""环境依赖检查（避免长命令换行截断问题）。

用法: .venv\Scripts\python.exe backend\scripts\check_env.py
"""
import importlib

CHECKS = [
    ("fastapi", "FastAPI"),
    ("langgraph", "LangGraph"),
    ("sqlalchemy", "SQLAlchemy"),
    ("redis", "redis"),
    ("psycopg", "psycopg"),
    ("locust", "locust"),
    ("pytest", "pytest"),
    ("paddle", "paddle"),
    ("paddleocr", "paddleocr"),
]

missing = []
for module, label in CHECKS:
    try:
        importlib.import_module(module)
        print(f"[OK] {label}")
    except ImportError:
        missing.append(module)
        print(f"[缺失] {label}")

if missing:
    print(f"\n还有 {len(missing)} 个依赖未安装: {', '.join(missing)}")
    raise SystemExit(1)
print("\n全部依赖 OK")
