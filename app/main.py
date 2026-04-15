"""Unleash Governance Proxy - FastAPI application."""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.config import get_settings
from app.logging_config import configure_logging, get_logger
from app.metrics import REQUEST_COUNT, REQUEST_LATENCY, metrics_handler
from app.routers import ai, audit, change_requests, flags

logger = get_logger(__name__)


def get_limiter_key(request: Request) -> str:
    """Rate limit by client IP, honouring X-Forwarded-For only for trusted hop counts.

    If `trusted_proxy_hops=N`, trust the N-th-from-right XFF entry (the edge proxy's
    upstream view of the client). If N=0, ignore XFF entirely and use the socket peer.
    This prevents trivial rate-limit evasion by clients forging XFF headers.
    """
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                idx = max(0, len(parts) - hops)
                return parts[idx]
    return get_remote_address(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan: configure logging, start background cleanup."""
    from app.idempotency_cleanup import run_cleanup_loop

    configure_logging()

    # Auto-create tables only when explicitly opted in (tests, local dev without Alembic).
    # In production, use `alembic upgrade head` before starting the process.
    if os.getenv("AUTO_CREATE_TABLES", "").lower() in ("1", "true", "yes"):
        from app.db import init_db
        init_db()
        logger.info("startup", event="auto_create_tables")

    logger.info("startup", event="app_started")
    cleanup_task = asyncio.create_task(run_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        logger.info("shutdown", event="app_stopped")


def _route_template(request: Request) -> str:
    """Resolve the matched route's path template (e.g. '/v1/flags/{flag_key}/...').

    Why: labeling metrics by raw request path blows up Prometheus cardinality
    when paths include IDs. The route template keeps the label set bounded.
    """
    route = request.scope.get("route")
    if isinstance(route, APIRoute):
        return route.path
    return "__unmatched__"


def create_app() -> FastAPI:
    settings = get_settings()
    rate_limit = f"{settings.rate_limit_per_minute}/minute" if settings.rate_limit_per_minute > 0 else "1000/minute"
    # Use Redis/Memcached storage when configured so limits are shared across
    # replicas. Falls back to in-process memory for local dev.
    limiter_kwargs = {"key_func": get_limiter_key, "default_limits": [rate_limit]}
    if settings.rate_limit_storage_uri:
        limiter_kwargs["storage_uri"] = settings.rate_limit_storage_uri
        limiter_kwargs["strategy"] = "moving-window"
    limiter = Limiter(**limiter_kwargs)

    app = FastAPI(
        title="Unleash Governance Proxy",
        description=(
            "Policy and audit layer for Unleash feature flags. "
            "Enforces authorization (OpenFGA or local policy), immutable audit logging, "
            "and change request workflows. AI features are optional and gated on OPENAI_API_KEY."
        ),
        version="0.3.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

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

    # OpenTelemetry (no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set).
    from app.observability import init_tracing
    init_tracing(app)

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        method = request.method
        response = await call_next(request)
        duration = time.perf_counter() - start
        template = _route_template(request)
        REQUEST_COUNT.labels(method=method, path=template, status=response.status_code).inc()
        REQUEST_LATENCY.labels(method=method, path=template).observe(duration)
        return response

    return app


app = create_app()
