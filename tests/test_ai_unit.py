"""Unit tests for the AI layer.

These tests mock the OpenAI HTTP call so they run offline and deterministically.
They exercise:
- llm.complete: ok path, 429 retry-then-success, terminal 4xx, timeout, budget cap
- llm.complete_json: JSON parse success + malformed-JSON recovery
- risk.get_risk_score: heuristic-only and LLM-augmented branches
- nl_query.interpret_nl_query: regex-only (no LLM) determinism
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.ai import llm as llm_mod
from app.ai.llm import LLMResult, complete, complete_json
from app.ai.nl_query import interpret_nl_query
from app.ai.risk import get_risk_score


def _mock_openai_response(content: str, *, prompt_tokens: int = 10, completion_tokens: int = 20) -> httpx.Response:
    body = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def enable_llm(monkeypatch):
    """Force the LLM-available gate on and reset the budget counter each test."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_FEATURES_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    llm_mod.reset_spend()
    yield
    llm_mod.reset_spend()


@pytest.mark.asyncio
async def test_complete_ok_records_tokens_and_cost():
    with patch("app.ai.llm.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_openai_response("hello"))
        mock_cls.return_value.__aenter__.return_value = mock_client

        r = await complete("hi", feature="test")

    assert r.ok is True
    assert r.text == "hello"
    assert r.input_tokens == 10
    assert r.output_tokens == 20
    assert r.cost_usd > 0
    assert r.error is None


@pytest.mark.asyncio
async def test_complete_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    responses = [httpx.Response(429, text="rate limited"), _mock_openai_response("ok")]

    async def fake_post(url, **kwargs):
        return responses.pop(0)

    with patch("app.ai.llm.httpx.AsyncClient") as mock_cls, \
         patch("app.ai.llm.asyncio.sleep", new=AsyncMock(return_value=None)):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value.__aenter__.return_value = mock_client

        r = await complete("hi", feature="test")

    assert r.ok is True
    assert r.text == "ok"


@pytest.mark.asyncio
async def test_complete_does_not_retry_on_4xx():
    with patch("app.ai.llm.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=httpx.Response(400, text="bad"))
        mock_cls.return_value.__aenter__.return_value = mock_client

        r = await complete("hi", feature="test")

    assert r.ok is False
    assert r.error and r.error.startswith("http:400")
    assert mock_client.post.await_count == 1


@pytest.mark.asyncio
async def test_complete_timeout_returns_error(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")
    from app.config import get_settings
    get_settings.cache_clear()

    with patch("app.ai.llm.httpx.AsyncClient") as mock_cls, \
         patch("app.ai.llm.asyncio.sleep", new=AsyncMock(return_value=None)):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))
        mock_cls.return_value.__aenter__.return_value = mock_client

        r = await complete("hi", feature="test")

    assert r.ok is False
    assert r.error == "transport:TimeoutException"


@pytest.mark.asyncio
async def test_complete_json_parse_failure():
    with patch("app.ai.llm.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_openai_response("not json {{"))
        mock_cls.return_value.__aenter__.return_value = mock_client

        result, parsed = await complete_json("hi", feature="test")

    assert parsed is None
    assert result.ok is False
    assert result.error == "json_parse_failed"


@pytest.mark.asyncio
async def test_complete_json_parse_ok():
    payload = {"key": "enable_dark_mode", "rationale": "clear"}
    with patch("app.ai.llm.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_openai_response(json.dumps(payload)))
        mock_cls.return_value.__aenter__.return_value = mock_client

        result, parsed = await complete_json("hi", feature="test")

    assert result.ok is True
    assert parsed == payload


@pytest.mark.asyncio
async def test_budget_cap_blocks_calls(monkeypatch):
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "0.0001")
    from app.config import get_settings
    get_settings.cache_clear()

    with patch("app.ai.llm.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_mock_openai_response("x", prompt_tokens=10000, completion_tokens=10000)
        )
        mock_cls.return_value.__aenter__.return_value = mock_client

        first = await complete("hi", feature="test")
        second = await complete("hi", feature="test")

    assert first.ok is True
    assert second.ok is False
    assert second.error == "budget_exhausted"


@pytest.mark.asyncio
async def test_risk_heuristic_only(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()

    out = await get_risk_score({
        "flag_key": "f",
        "environment": "prod",
        "desired_changes": {"enabled": True, "type": "release"},
        "strategies": [{"name": "flexibleRollout"}],
    })
    assert out["source"] == "heuristic"
    assert out["level"] in {"low", "medium", "high"}
    assert out["score"] >= 0.6  # prod + enabled + release + strategy


@pytest.mark.asyncio
async def test_risk_llm_merge():
    payload = {
        "level": "high",
        "explanation": "Enabling in prod without canary.",
        "concerns": ["no canary", "release type"],
    }
    with patch("app.ai.llm.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_openai_response(json.dumps(payload)))
        mock_cls.return_value.__aenter__.return_value = mock_client

        out = await get_risk_score({
            "flag_key": "f",
            "environment": "prod",
            "desired_changes": {"enabled": True},
        })

    assert out["source"] == "heuristic+llm"
    assert out["explanation"] == "Enabling in prod without canary."
    assert len(out["concerns"]) == 2


@pytest.mark.asyncio
async def test_nl_query_regex_only(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()

    out = await interpret_nl_query("events by alice@example.com in the last 7 days", None, None, None)
    assert out["interpreted"]["actor"] == "alice@example.com"
    assert out["interpreted"]["start"] is not None
    assert out["interpreted"]["source"] == "regex"
