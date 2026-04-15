"""Prometheus metrics for AI/LLM calls: latency, tokens, cost, errors."""

from prometheus_client import Counter, Histogram

LLM_CALLS_TOTAL = Counter(
    "governance_llm_calls_total",
    "LLM completion calls",
    ["feature", "model", "outcome"],
)
LLM_LATENCY_SECONDS = Histogram(
    "governance_llm_latency_seconds",
    "LLM completion latency",
    ["feature", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0),
)
LLM_TOKENS_TOTAL = Counter(
    "governance_llm_tokens_total",
    "Tokens consumed by LLM calls",
    ["feature", "model", "kind"],
)
LLM_COST_USD_TOTAL = Counter(
    "governance_llm_cost_usd_total",
    "Cumulative estimated LLM cost in USD",
    ["feature", "model"],
)
