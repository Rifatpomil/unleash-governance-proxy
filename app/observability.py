"""OpenTelemetry wiring.

Traces are the missing half of our observability story (metrics tell you *what*
happened, traces tell you *why*). We instrument three layers:
- FastAPI: one span per request, with the matched route template as the span name.
- httpx: outbound calls to OpenAI and Unleash become child spans, so LLM latency
  is attributable per request.
- SQLAlchemy: query spans let us attribute DB time inside an endpoint.

Nothing here is required. If `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, the module
is a no-op — imports stay cheap and prod deployments can opt in.
"""

from __future__ import annotations

from typing import Optional

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_initialized = False


def init_tracing(app) -> None:
    """Initialize OTLP tracing for the given FastAPI app. Idempotent."""
    global _initialized
    if _initialized:
        return

    settings = get_settings()
    endpoint: Optional[str] = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        logger.info("otel_disabled", reason="no_endpoint")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ImportError as e:
        logger.warning("otel_deps_missing", error=str(e))
        return

    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: settings.otel_service_name}),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_traces_sampler_ratio)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics,/health")
    HTTPXClientInstrumentor().instrument()

    try:
        from app.db.session import get_engine
        SQLAlchemyInstrumentor().instrument(engine=get_engine())
    except Exception as e:
        logger.warning("otel_sqlalchemy_instrument_failed", error=str(e))

    _initialized = True
    logger.info("otel_initialized", endpoint=endpoint, service=settings.otel_service_name)
