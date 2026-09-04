#!/bin/sh
# 容器入口：先初始化数据库（幂等），再启动应用主进程。
set -e

python scripts/init_db.py || { echo "数据库初始化失败，容器退出"; exit 1; }

# 把剩余参数（如 uvicorn / worker 命令）作为主进程执行
exec "$@"