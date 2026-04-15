"""AI endpoints: async summarization, risk, NL query, suggestions, investigator agent."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.agent import run_agent, run_agent_stream
from app.ai.anomaly import detect_anomalies
from app.ai.llm import is_llm_available
from app.ai.nl_query import (
    extract_actor_from_query,
    interpret_nl_query,
    parse_relative_time,
)
from app.ai.prompts import VERSION as PROMPT_VERSION
from app.ai.risk import get_risk_score
from app.ai.suggestions import suggest_flag_name, suggest_strategy_for_rollout
from app.ai.summarizer import summarize_audit_logs, summarize_change_requests
from app.auth import get_current_user
from app.config import get_settings
from app.db import get_db
from app.db.models import AuditLog, ChangeRequest

router = APIRouter(prefix="/v1/ai", tags=["ai"])

FEATURES = ["summarize", "risk", "nl_query", "suggestions", "anomaly", "agent"]


class NLQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=50, ge=1, le=200)


class SuggestFlagRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    project_context: Optional[str] = Field(default=None, max_length=200)


class SuggestStrategyRequest(BaseModel):
    flag_key: str = Field(..., min_length=1, max_length=255)
    target_audience: Optional[str] = Field(default=None, max_length=200)
    percentage: Optional[int] = Field(default=None, ge=0, le=100)


class AgentRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)


def _status_payload() -> dict[str, Any]:
    s = get_settings()
    return {
        "ai_available": is_llm_available(),
        "features": FEATURES,
        "model": s.llm_model,
        "prompt_version": PROMPT_VERSION,
    }


@router.get("/status/public")
def ai_status_public():
    """Public: whether AI is configured. No auth."""
    return _status_payload()


@router.get("/status")
def ai_status(user: dict = Depends(get_current_user)):
    return _status_payload()


@router.get("/summarize/change-requests")
async def summarize_change_requests_endpoint(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = db.query(ChangeRequest).order_by(ChangeRequest.created_at.desc()).limit(limit).all()
    data = [
        {
            "id": cr.id,
            "flag_key": cr.flag_key,
            "status": cr.status,
            "desired_changes": cr.desired_changes,
            "created_at": cr.created_at.isoformat() if cr.created_at else None,
        }
        for cr in items
    ]
    summary = await summarize_change_requests(data)
    return {"summary": summary, "count": len(data)}


@router.get("/summarize/audit")
async def summarize_audit_endpoint(
    limit: int = Query(25, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entries = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    data = [
        {
            "action": e.action,
            "actor": e.actor,
            "resource_type": e.resource_type,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]
    summary = await summarize_audit_logs(data)
    return {"summary": summary, "count": len(data)}


@router.get("/risk/{change_request_id}")
async def risk_score(
    change_request_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cr = db.query(ChangeRequest).filter(ChangeRequest.id == change_request_id).first()
    if not cr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change request not found")
    payload = {
        "flag_key": cr.flag_key,
        "desired_changes": cr.desired_changes,
        "environment": cr.environment,
        "strategies": cr.strategies,
    }
    return await get_risk_score(payload)


@router.post("/nl-query")
async def nl_query(
    body: NLQueryRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    time_range = parse_relative_time(body.query)
    actor = extract_actor_from_query(body.query)
    start = time_range[0] if time_range else None
    end = time_range[1] if time_range else None
    result = await interpret_nl_query(body.query, actor, start, end)

    interpreted = result["interpreted"]
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if interpreted.get("start"):
        from datetime import datetime as _dt
        q = q.filter(AuditLog.created_at >= _dt.fromisoformat(interpreted["start"]))
    if interpreted.get("end"):
        from datetime import datetime as _dt
        q = q.filter(AuditLog.created_at <= _dt.fromisoformat(interpreted["end"]))
    if interpreted.get("actor"):
        q = q.filter(AuditLog.actor == interpreted["actor"])
    if interpreted.get("action"):
        q = q.filter(AuditLog.action == interpreted["action"])

    entries = q.limit(body.limit).all()
    result["results_count"] = len(entries)
    result["sample_entries"] = [
        {
            "action": e.action,
            "actor": e.actor,
            "resource_type": e.resource_type,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries[:10]
    ]
    return result


@router.post("/suggest/flag-name")
async def suggest_flag(
    body: SuggestFlagRequest,
    user: dict = Depends(get_current_user),
):
    return await suggest_flag_name(body.description, body.project_context)


@router.post("/suggest/strategy")
async def suggest_strategy(
    body: SuggestStrategyRequest,
    user: dict = Depends(get_current_user),
):
    return await suggest_strategy_for_rollout(body.flag_key, body.target_audience, body.percentage)


@router.get("/anomalies")
def anomalies(
    hours: int = Query(72, ge=6, le=168),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return detect_anomalies(db, hours=hours)


@router.post("/agent/investigate")
async def agent_investigate(
    body: AgentRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tool-use agent that answers audit questions by calling typed DB tools.

    The agent sees only the tools declared in app.ai.agent.TOOLS — it cannot run
    arbitrary SQL. Each tool is type-validated and read-only.
    """
    result = await run_agent(body.question, db)
    if not result.ok and result.error == "llm_disabled":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM not configured; /v1/ai/nl-query provides a regex fallback.",
        )
    return {
        "ok": result.ok,
        "answer": result.answer,
        "error": result.error,
        "steps": [
            {"tool": s.tool, "arguments": s.arguments, "result": s.result} for s in result.steps
        ],
        "prompt_version": result.prompt_version,
        "model": result.model,
    }


@router.post("/agent/investigate/stream")
async def agent_investigate_stream(
    body: AgentRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Server-Sent Events stream of agent events (start, tool_call, answer, end).

    Each event is a line `data: <json>\n\n`. Clients can render tool calls as
    they arrive instead of waiting for the full turn loop.
    """
    import json as _json

    async def event_source():
        async for event in run_agent_stream(body.question, db):
            yield f"data: {_json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/insights")
async def insights(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cr_items = db.query(ChangeRequest).order_by(ChangeRequest.created_at.desc()).limit(15).all()
    cr_data = [
        {"flag_key": cr.flag_key, "status": cr.status, "desired_changes": cr.desired_changes}
        for cr in cr_items
    ]
    audit_entries = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(20).all()
    audit_data = [
        {"action": e.action, "actor": e.actor, "resource_type": e.resource_type}
        for e in audit_entries
    ]
    return {
        "change_requests_summary": await summarize_change_requests(cr_data),
        "audit_summary": await summarize_audit_logs(audit_data),
        "anomalies": detect_anomalies(db, hours=72),
        "ai_available": is_llm_available(),
        "prompt_version": PROMPT_VERSION,
    }
