"""Prometheus metrics."""

from prometheus_client import Counter, Histogram, generate_latest

# Request metrics
REQUEST_COUNT = Counter(
    "governance_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "governance_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# Business metrics
CHANGE_REQUESTS_CREATED = Counter(
    "governance_change_requests_created_total",
    "Change requests created",
    ["flag_key"],
)
CHANGE_REQUESTS_APPLIED = Counter(
    "governance_change_requests_applied_total",
    "Change requests applied to Unleash",
    ["flag_key", "status"],
)
UNLEASH_CLIENT_ERRORS = Counter(
    "governance_unleash_client_errors_total",
    "Unleash API client errors",
    ["operation", "error_type"],
)
IDEMPOTENCY_CLEANUP_DELETED = Counter(
    "governance_idempotency_keys_deleted_total",
    "Expired idempotency keys purged",
)


def metrics_handler():
    """Return Prometheus metrics in text format."""
    return generate_latest()
