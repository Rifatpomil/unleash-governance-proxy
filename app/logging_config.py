"""Structured logging configuration."""

import logging
import os
import sys

import structlog


def configure_logging(log_level: str = None) -> None:
    """Configure structlog for structured JSON logging."""
    level = getattr(logging, (log_level or os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer() if _use_json() else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def _use_json() -> bool:
    """Use JSON format in production (when LOG_FORMAT=json)."""
    import os
    return os.getenv("LOG_FORMAT", "").lower() == "json"


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger."""
    return structlog.get_logger(name)
