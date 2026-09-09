"""FastAPI 应用入口：路由挂载 + 统一错误格式（不泄露堆栈）。"""
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin_users, auth, cases, dashboard, events, intent, review_tasks, security, telemetry
from app.api import cart, customer_auth, orders, products
from app.batch import api as batch_api
from app.core.config import get_settings
from app.eval import api as eval_api
from app.infrastructure.storage import UPLOAD_DIR
from app.rag import chat as rag_chat

logger = logging.getLogger("uvicorn.error")
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """统一错误格式：{"code": "...", "message": "..."}。"""
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底 500：不向客户端泄露堆栈。"""
    logger.exception("Unhandled error: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": "服务器内部错误"},
    )


@app.get("/api/v1/health", tags=["system"])
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "env": settings.env}


app.include_router(auth.router)
app.include_router(admin_users.router)
app.include_router(cases.router)
app.include_router(intent.router)
app.include_router(security.router)
app.include_router(telemetry.router)
app.include_router(events.router)
app.include_router(review_tasks.router)
app.include_router(dashboard.router)
app.include_router(batch_api.router)
app.include_router(eval_api.router)
app.include_router(customer_auth.router)
app.include_router(customer_auth.buyer_cases_router)
app.include_router(products.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(rag_chat.router)

# L-5 修复：凭证上传目录静态服务（image_url 为 /uploads/<uuid>.ext，此处直接可下载/预览）
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
