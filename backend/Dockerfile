# 后端/Worker 镜像（API 与 Worker 共用，通过 command 区分）
# 基于官方 Python 3.12（任务三验收：部署不依赖宿主机 anaconda）
FROM python:3.12-slim

WORKDIR /app

# 系统依赖：PaddleOCR 推理需要 libgl1/libgomp；健康检查需要 curl
# 换清华 apt 源加速（deb.debian.org 国内极慢）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖分层缓存（先复制 requirements 再复制代码，利于 Docker 缓存）
# 使用清华镜像加速（容器内默认 pypi.org 国内极慢，无需外网）
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
COPY backend/requirements.txt requirements.txt
COPY backend/requirements-dev.txt requirements-dev.txt
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt

# OCR 本地模型（CPU）+ paddleocr（体积大，单独层，缓存友好）
RUN pip install --no-cache-dir -i ${PIP_INDEX} \
    paddlepaddle==3.3.1 \
    "paddleocr==3.7.0"

COPY backend/ .

# 健康检查脚本（worker 用：检查 Redis 依赖存活）
COPY backend/scripts/docker_health.py scripts/docker_health.py

# 容器入口：先幂等初始化数据库（alembic+seed），再启动应用
COPY backend/scripts/entrypoint.sh scripts/entrypoint.sh
COPY backend/scripts/init_db.py scripts/init_db.py
RUN chmod +x scripts/entrypoint.sh

EXPOSE 8000

# API 默认命令；worker 由 compose 覆盖 command
# 先跑 entrypoint 初始化库，再 exec 主进程
# --workers 4：多进程提升吞吐（Phase 11 优化；无状态设计，PG/Redis 共享安全）
ENTRYPOINT ["scripts/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
