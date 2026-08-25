from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.api.chat import router as chat_router
from app.api.collections import router as collections_router
from app.api.dependencies import RequestContextMiddleware
from app.api.documents import router as documents_router
from app.api.errors import AppError
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.search import router as search_router
from app.api.sessions import router as sessions_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.services.arq_enqueuer import ArqEnqueuer

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_storage_dirs()
    logger = get_logger("startup")
    logger.info(
        "app_starting",
        env=settings.env,
        host=settings.host,
        port=settings.port,
    )
    try:
        yield
    finally:
        logger.info("app_stopping")
        await dispose_engine()
        logger.info("app_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Paper RAG Assistant",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(collections_router)
    app.include_router(jobs_router)
    app.include_router(search_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": request_id,
                }
            },
        )

    # Dispatch ARQ enqueue only after the response is committed (spec §10:
    # API creates document+job in one DB transaction, then enqueues).
    @app.middleware("http")
    async def enqueue_after_commit(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        pending = getattr(request.state, "pending_enqueue", None)
        if pending is not None:
            job_id, kind, document_id, attempt = pending
            try:
                enqueuer = ArqEnqueuer(get_settings())
                await enqueuer.enqueue(job_id, kind, document_id=document_id, attempt=attempt)
            except Exception:
                logger.exception(
                    "enqueue_failed", job_id=job_id, kind=kind, document_id=document_id
                )
                # Job stays queued; reconciliation (Phase 9) will recover it.
        return response

    return app


app = create_app()
