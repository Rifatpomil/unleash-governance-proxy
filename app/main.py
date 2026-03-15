"""Unleash Governance Proxy - FastAPI application."""

import asyncio
import time
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.config import get_settings
from app.db import init_db
from app.logging_config import configure_logging, get_logger
from app.metrics import REQUEST_COUNT, REQUEST_LATENCY, metrics_handler
from app.routers import ai, audit, change_requests, flags

logger = get_logger(__name__)


def get_limiter_key(request: Request) -> str:
    """Rate limit by IP (or X-Forwarded-For if behind proxy)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: init DB, configure logging, start cleanup task."""
    from app.idempotency_cleanup import run_cleanup_loop

    configure_logging()
    init_db()
    logger.info("startup", event="app_started")

    # Start idempotency cleanup task in background
    cleanup_task = asyncio.create_task(run_cleanup_loop())

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("shutdown", event="app_stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    rate_limit = f"{settings.rate_limit_per_minute}/minute" if settings.rate_limit_per_minute > 0 else "1000/minute"
    limiter = Limiter(key_func=get_limiter_key, default_limits=[rate_limit])

    app = FastAPI(
        title="Unleash Governance Proxy",
        description=(
            "Policy and audit layer for Unleash feature flags. "
            "Enforces authorization (OpenFGA or local policy), "
            "immutable audit logging, and change request workflows."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # Metrics and health (exempt from rate limit)
    @app.get("/metrics")
    @limiter.exempt
    def metrics() -> Response:
        return Response(metrics_handler(), media_type="text/plain")

    @app.get("/health")
    @limiter.exempt
    def health():
        return {"status": "ok"}

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        @limiter.exempt
        def dashboard():
            return FileResponse(static_dir / "index.html")

    app.include_router(flags.router)
    app.include_router(change_requests.router)
    app.include_router(audit.router)
    app.include_router(ai.router)

    # Metrics middleware
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        path = request.scope.get("path", "")
        method = request.method
        response = await call_next(request)
        duration = time.perf_counter() - start
        status = response.status_code
        REQUEST_COUNT.labels(method=method, path=path, status=status).inc()
        REQUEST_LATENCY.labels(method=method, path=path).observe(duration)
        return response

    return app


app = create_app()
