"""Async LLM client: timeouts, bounded retries, token/cost accounting, structured outputs.

Design goals (why this file looks the way it does):
- Never block the FastAPI event loop: use httpx.AsyncClient directly against the OpenAI
  REST API. We avoid the official SDK's sync client on purpose — it would block the loop.
- Every call is observable: latency histogram, call counter by outcome, token counter,
  estimated cost counter. Errors are logged with structured context, never swallowed.
- Every call is bounded: explicit timeout, bounded retry with exponential backoff for
  429/5xx/network errors only, and a soft per-process USD budget cap.
- Callers get a typed result (LLMResult) instead of Optional[str], so upstream code
  can distinguish "LLM disabled" from "LLM errored" from "LLM returned text".
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.ai.metrics import (
    LLM_CALLS_TOTAL,
    LLM_COST_USD_TOTAL,
    LLM_LATENCY_SECONDS,
    LLM_TOKENS_TOTAL,
)
from app.ai.prompts import SYSTEM_GOVERNANCE, VERSION as PROMPT_VERSION
from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)


# USD per 1K tokens — kept here so cost estimates live next to the code that spends them.
# Update when pricing changes; not a source of truth, just a budget guardrail.
_PRICE_PER_1K_TOKENS = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
}

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

_spent_usd: float = 0.0


@dataclass
class LLMResult:
    """Result of an LLM call. `text` is None iff `ok` is False or LLM disabled."""

    ok: bool
    text: Optional[str] = None
    model: str = ""
    feature: str = ""
    prompt_version: str = PROMPT_VERSION
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    error: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


def is_llm_available() -> bool:
    s = get_settings()
    return bool(s.openai_api_key and s.ai_features_enabled)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _PRICE_PER_1K_TOKENS.get(model)
    if not rates:
        return 0.0
    return (input_tokens / 1000.0) * rates["input"] + (output_tokens / 1000.0) * rates["output"]


def _budget_exhausted() -> bool:
    cap = get_settings().llm_monthly_budget_usd
    return cap > 0 and _spent_usd >= cap


def reset_spend() -> None:
    """Test hook: reset in-process spend accumulator."""
    global _spent_usd
    _spent_usd = 0.0


async def complete(
    prompt: str,
    *,
    feature: str,
    system: str = SYSTEM_GOVERNANCE,
    response_json: bool = False,
    max_output_tokens: Optional[int] = None,
    temperature: float = 0.2,
) -> LLMResult:
    """Run a chat completion. Never raises for LLM-side errors — returns LLMResult.ok=False.

    Args:
        feature: short label for metrics/logging (e.g. "risk_explain"). Required so
            every call is attributable on the dashboard.
        response_json: when True, request JSON object mode from the API. Callers
            should still validate — json_mode reduces but does not eliminate drift.
    """
    global _spent_usd
    s = get_settings()

    if not is_llm_available():
        return LLMResult(ok=False, feature=feature, model=s.llm_model, error="llm_disabled")

    if _budget_exhausted():
        logger.warning("llm_budget_exhausted", feature=feature, spent_usd=_spent_usd)
        LLM_CALLS_TOTAL.labels(feature=feature, model=s.llm_model, outcome="budget").inc()
        return LLMResult(ok=False, feature=feature, model=s.llm_model, error="budget_exhausted")

    payload: dict[str, Any] = {
        "model": s.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_output_tokens or s.llm_max_output_tokens,
        "temperature": temperature,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {s.openai_api_key}",
        "Content-Type": "application/json",
    }

    attempt = 0
    start = time.perf_counter()
    last_error = "unknown"
    async with httpx.AsyncClient(timeout=s.llm_timeout_seconds) as client:
        while True:
            attempt += 1
            try:
                resp = await client.post(_OPENAI_URL, headers=headers, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = f"transport:{type(e).__name__}"
                if attempt > s.llm_max_retries:
                    break
                await asyncio.sleep(min(2 ** attempt, 8))
                continue

            if resp.status_code == 200:
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                text = (choice.get("message") or {}).get("content") or ""
                usage = data.get("usage") or {}
                input_tokens = int(usage.get("prompt_tokens", 0))
                output_tokens = int(usage.get("completion_tokens", 0))
                cost = _estimate_cost(s.llm_model, input_tokens, output_tokens)
                _spent_usd += cost
                latency = time.perf_counter() - start

                LLM_CALLS_TOTAL.labels(feature=feature, model=s.llm_model, outcome="ok").inc()
                LLM_LATENCY_SECONDS.labels(feature=feature, model=s.llm_model).observe(latency)
                LLM_TOKENS_TOTAL.labels(feature=feature, model=s.llm_model, kind="input").inc(input_tokens)
                LLM_TOKENS_TOTAL.labels(feature=feature, model=s.llm_model, kind="output").inc(output_tokens)
                LLM_COST_USD_TOTAL.labels(feature=feature, model=s.llm_model).inc(cost)
                logger.info(
                    "llm_ok",
                    feature=feature,
                    model=s.llm_model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=round(cost, 6),
                    latency_s=round(latency, 3),
                    prompt_version=PROMPT_VERSION,
                )
                return LLMResult(
                    ok=True,
                    text=text.strip(),
                    model=s.llm_model,
                    feature=feature,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    latency_s=latency,
                    raw=data,
                )

            # Retry on 429 / 5xx only. 4xx other than 429 is a caller bug — don't retry.
            if resp.status_code in (429,) or resp.status_code >= 500:
                last_error = f"http:{resp.status_code}"
                if attempt > s.llm_max_retries:
                    break
                await asyncio.sleep(min(2 ** attempt, 8))
                continue

            last_error = f"http:{resp.status_code}:{resp.text[:200]}"
            break

    latency = time.perf_counter() - start
    LLM_CALLS_TOTAL.labels(feature=feature, model=s.llm_model, outcome="error").inc()
    LLM_LATENCY_SECONDS.labels(feature=feature, model=s.llm_model).observe(latency)
    logger.error(
        "llm_error",
        feature=feature,
        model=s.llm_model,
        error=last_error,
        attempts=attempt,
        latency_s=round(latency, 3),
    )
    return LLMResult(
        ok=False,
        feature=feature,
        model=s.llm_model,
        error=last_error,
        latency_s=latency,
    )


async def complete_json(
    prompt: str,
    *,
    feature: str,
    system: str = SYSTEM_GOVERNANCE,
    max_output_tokens: Optional[int] = None,
    temperature: float = 0.0,
) -> tuple[LLMResult, Optional[dict[str, Any]]]:
    """Run a JSON-mode completion and parse the result. Returns (result, parsed-or-None)."""
    result = await complete(
        prompt,
        feature=feature,
        system=system,
        response_json=True,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )
    if not result.ok or not result.text:
        return result, None
    try:
        return result, json.loads(result.text)
    except json.JSONDecodeError as e:
        logger.warning("llm_json_parse_failed", feature=feature, error=str(e), text=result.text[:200])
        result.ok = False
        result.error = "json_parse_failed"
        return result, None
