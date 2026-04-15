"""End-to-end eval for the audit investigator agent.

We mock the OpenAI tool-use loop (so no network, no flakiness) and verify:
1. The agent dispatches the tool calls the model requests.
2. Tool inputs are validated (unknown tool names don't crash).
3. The tool-call transcript matches the documented contract.
4. The bounded loop terminates when the model stops asking for tools.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.ai.agent import run_agent
from app.db.models import AuditLog


def _tool_call_response(tool_name: str, args: dict, call_id: str = "call_1") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": json.dumps(args)},
                    }],
                }
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        },
    )


def _final_answer(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        },
    )


@pytest.fixture(autouse=True)
def llm_enabled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_FEATURES_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_agent_dispatches_tool_and_terminates(db_session):
    db_session.add(AuditLog(actor="alice", action="change_request_applied", resource_type="change_request"))
    db_session.commit()

    responses = [
        _tool_call_response("count_audit_events", {"actor": "alice"}, call_id="c1"),
        _final_answer("Alice has 1 audit event."),
    ]

    async def fake_post(url, **kwargs):
        return responses.pop(0)

    with patch("app.ai.agent.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value.__aenter__.return_value = mock_client

        result = await run_agent("How many events did alice create?", db_session)

    assert result.ok is True
    assert result.answer == "Alice has 1 audit event."
    assert len(result.steps) == 1
    assert result.steps[0].tool == "count_audit_events"
    assert result.steps[0].result["count"] == 1


@pytest.mark.asyncio
async def test_agent_survives_unknown_tool(db_session):
    responses = [
        _tool_call_response("drop_database", {}, call_id="c1"),
        _final_answer("Could not answer."),
    ]

    async def fake_post(url, **kwargs):
        return responses.pop(0)

    with patch("app.ai.agent.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=fake_post)
        mock_cls.return_value.__aenter__.return_value = mock_client

        result = await run_agent("delete everything", db_session)

    assert result.ok is True
    assert result.steps[0].result == {"error": "unknown_tool:drop_database"}


@pytest.mark.asyncio
async def test_agent_enforces_turn_limit(db_session):
    # Always return another tool call — the loop must stop at MAX_TOOL_TURNS.
    def tool_call_forever(*_a, **_kw):
        return _tool_call_response("count_audit_events", {}, call_id="c")

    with patch("app.ai.agent.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=tool_call_forever)
        mock_cls.return_value.__aenter__.return_value = mock_client

        result = await run_agent("loop forever", db_session)

    assert result.ok is False
    assert result.error == "turn_limit_reached"


@pytest.mark.asyncio
async def test_agent_disabled_without_key(monkeypatch, db_session):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()

    result = await run_agent("question", db_session)
    assert result.ok is False
    assert result.error == "llm_disabled"
