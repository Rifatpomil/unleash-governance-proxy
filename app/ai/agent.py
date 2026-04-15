"""Audit investigator agent: LLM tool-use over governance data.

Why this exists: natural-language search over audit events is useful, but a single
prompt can't reliably join "flags recently changed by X" with "how risky were those
changes" without hallucinating. This agent gives the model a tiny, typed toolbox
(count events, list events, count change requests) and runs a bounded tool-use loop.

Invariants the agent relies on:
- All tools take **only** the arguments the model provides. We validate every field.
- All tools read from the *caller's* SQLAlchemy session — one DB session per invocation,
  so an agent run is transactionally consistent.
- Tool loop is bounded (max 4 turns) and every tool call is logged.
- If the LLM is disabled we return a deterministic "unavailable" result rather than
  silently falling back — callers can choose whether to degrade.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional

import httpx

from app.ai.llm import is_llm_available
from app.ai.metrics import LLM_CALLS_TOTAL, LLM_LATENCY_SECONDS
from app.ai.prompts import AGENT_SYSTEM, VERSION as PROMPT_VERSION
from app.config import get_settings
from app.db.models import AuditLog, ChangeRequest
from app.logging_config import get_logger

logger = get_logger(__name__)

MAX_TOOL_TURNS = 4
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "count_audit_events",
            "description": "Count audit events, optionally filtered by actor/action/time window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actor": {"type": "string", "description": "Exact actor id/email"},
                    "action": {"type": "string", "description": "Exact action name"},
                    "hours": {"type": "integer", "description": "Look-back window in hours (1-720)", "minimum": 1, "maximum": 720},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_audit_events",
            "description": "List up to 20 recent audit events, optionally filtered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actor": {"type": "string"},
                    "action": {"type": "string"},
                    "hours": {"type": "integer", "minimum": 1, "maximum": 720},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_change_requests_by_status",
            "description": "Group change requests by status (pending/approved/applied/rejected).",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


@dataclass
class AgentStep:
    """One turn of the agent loop: tool call name, args, and tool-side result."""

    tool: str
    arguments: dict[str, Any]
    result: Any


@dataclass
class AgentResult:
    ok: bool
    answer: Optional[str] = None
    steps: list[AgentStep] = field(default_factory=list)
    error: Optional[str] = None
    prompt_version: str = PROMPT_VERSION
    model: str = ""


def _bound_hours(v: Any, default: int = 168) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(1, min(720, n))


def _tool_count_audit_events(db, args: dict[str, Any]) -> dict[str, Any]:
    q = db.query(AuditLog)
    if isinstance(args.get("actor"), str):
        q = q.filter(AuditLog.actor == args["actor"])
    if isinstance(args.get("action"), str):
        q = q.filter(AuditLog.action == args["action"])
    if "hours" in args:
        since = datetime.now(timezone.utc) - timedelta(hours=_bound_hours(args["hours"]))
        q = q.filter(AuditLog.created_at >= since)
    return {"count": q.count()}


def _tool_list_audit_events(db, args: dict[str, Any]) -> dict[str, Any]:
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if isinstance(args.get("actor"), str):
        q = q.filter(AuditLog.actor == args["actor"])
    if isinstance(args.get("action"), str):
        q = q.filter(AuditLog.action == args["action"])
    if "hours" in args:
        since = datetime.now(timezone.utc) - timedelta(hours=_bound_hours(args["hours"]))
        q = q.filter(AuditLog.created_at >= since)
    limit = max(1, min(20, int(args.get("limit", 10))))
    rows = q.limit(limit).all()
    return {
        "events": [
            {
                "action": r.action,
                "actor": r.actor,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


def _tool_count_cr_by_status(db, _args: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import func
    rows = db.query(ChangeRequest.status, func.count(ChangeRequest.id)).group_by(ChangeRequest.status).all()
    return {"by_status": {s: n for s, n in rows}}


_DISPATCH = {
    "count_audit_events": _tool_count_audit_events,
    "list_audit_events": _tool_list_audit_events,
    "count_change_requests_by_status": _tool_count_cr_by_status,
}


async def run_agent(question: str, db) -> AgentResult:
    """Run the audit agent against the live DB session for `question`."""
    s = get_settings()
    if not is_llm_available():
        return AgentResult(ok=False, error="llm_disabled")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": question},
    ]
    steps: list[AgentStep] = []

    headers = {
        "Authorization": f"Bearer {s.openai_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=s.llm_timeout_seconds) as client:
        for turn in range(MAX_TOOL_TURNS):
            payload = {
                "model": s.llm_model,
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 0.0,
                "max_tokens": s.llm_max_output_tokens,
            }
            try:
                resp = await client.post(_OPENAI_URL, headers=headers, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                logger.error("agent_transport_error", turn=turn, error=str(e))
                LLM_CALLS_TOTAL.labels(feature="agent", model=s.llm_model, outcome="error").inc()
                return AgentResult(ok=False, error=f"transport:{type(e).__name__}", steps=steps, model=s.llm_model)

            if resp.status_code != 200:
                logger.error("agent_http_error", turn=turn, status=resp.status_code, body=resp.text[:200])
                LLM_CALLS_TOTAL.labels(feature="agent", model=s.llm_model, outcome="error").inc()
                return AgentResult(ok=False, error=f"http:{resp.status_code}", steps=steps, model=s.llm_model)

            LLM_CALLS_TOTAL.labels(feature="agent", model=s.llm_model, outcome="ok").inc()
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            # Terminal: the model answered without (more) tool calls.
            if not tool_calls:
                answer = (message.get("content") or "").strip()
                logger.info("agent_done", turns=turn, steps=len(steps), answer_len=len(answer))
                return AgentResult(ok=True, answer=answer, steps=steps, model=s.llm_model)

            # Append the assistant message (with tool_calls) so the transcript stays valid.
            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            })

            # Execute each tool call, append the tool results.
            for call in tool_calls:
                fn = (call.get("function") or {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                handler = _DISPATCH.get(name)
                if handler is None:
                    result: Any = {"error": f"unknown_tool:{name}"}
                else:
                    try:
                        result = handler(db, args)
                    except Exception as e:
                        logger.error("agent_tool_error", tool=name, error=str(e))
                        result = {"error": f"tool_raised:{type(e).__name__}"}

                steps.append(AgentStep(tool=name, arguments=args, result=result))
                logger.info("agent_tool_call", tool=name, arguments=args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": json.dumps(result, default=str),
                })

    logger.warning("agent_turn_limit_reached", steps=len(steps))
    return AgentResult(
        ok=False,
        error="turn_limit_reached",
        steps=steps,
        model=s.llm_model,
    )


async def run_agent_stream(question: str, db) -> AsyncIterator[dict[str, Any]]:
    """Streaming variant of run_agent. Yields structured events as the agent works.

    Event schema (each is JSON-serializable):
        {"type": "start"}
        {"type": "tool_call", "tool": str, "arguments": dict, "result": Any}
        {"type": "answer", "text": str}
        {"type": "error", "error": str}
        {"type": "end", "ok": bool, "steps": int}

    Callers (e.g. SSE endpoint) can format these however they want. Keeping the
    transport decoupled means non-HTTP consumers (tests, CLIs) use the same stream.
    """
    s = get_settings()
    yield {"type": "start", "model": s.llm_model, "prompt_version": PROMPT_VERSION}

    if not is_llm_available():
        yield {"type": "error", "error": "llm_disabled"}
        yield {"type": "end", "ok": False, "steps": 0}
        return

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": question},
    ]
    headers = {
        "Authorization": f"Bearer {s.openai_api_key}",
        "Content-Type": "application/json",
    }
    step_count = 0

    async with httpx.AsyncClient(timeout=s.llm_timeout_seconds) as client:
        for turn in range(MAX_TOOL_TURNS):
            payload = {
                "model": s.llm_model,
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 0.0,
                "max_tokens": s.llm_max_output_tokens,
            }
            try:
                resp = await client.post(_OPENAI_URL, headers=headers, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                yield {"type": "error", "error": f"transport:{type(e).__name__}"}
                yield {"type": "end", "ok": False, "steps": step_count}
                return

            if resp.status_code != 200:
                yield {"type": "error", "error": f"http:{resp.status_code}"}
                yield {"type": "end", "ok": False, "steps": step_count}
                return

            choice = (resp.json().get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                answer = (message.get("content") or "").strip()
                yield {"type": "answer", "text": answer}
                yield {"type": "end", "ok": True, "steps": step_count}
                return

            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                fn = (call.get("function") or {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                handler = _DISPATCH.get(name)
                if handler is None:
                    result: Any = {"error": f"unknown_tool:{name}"}
                else:
                    try:
                        result = handler(db, args)
                    except Exception as e:
                        result = {"error": f"tool_raised:{type(e).__name__}"}

                step_count += 1
                yield {"type": "tool_call", "tool": name, "arguments": args, "result": result}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": json.dumps(result, default=str),
                })

    yield {"type": "error", "error": "turn_limit_reached"}
    yield {"type": "end", "ok": False, "steps": step_count}
