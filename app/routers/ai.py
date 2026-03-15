"""AI-powered endpoints: summarization, risk scoring, NL query, suggestions, insights."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.anomaly import detect_anomalies
from app.ai.llm import is_llm_available
from app.ai.nl_query import interpret_nl_query
from app.ai.risk import get_risk_score
from app.ai.summarizer import summarize_audit_logs, summarize_change_requests
from app.ai.suggestions import suggest_flag_name, suggest_strategy_for_rollout
from app.auth import get_current_user
from app.db import get_db
from app.db.models import AuditLog, ChangeRequest

router = APIRouter(prefix="/v1/ai", tags=["ai"])


class NLQueryRequest(BaseModel):
    """Natural language query for audit/change requests."""

    query: str = Field(..., description="e.g. 'Show changes from last 7 days'")
    limit: int = Field(default=50, ge=1, le=200)


class SuggestFlagRequest(BaseModel):
    """Request for flag name suggestion."""

    description: str = Field(..., description="Short description of the feature")
    project_context: Optional[str] = Field(default=None)


class SuggestStrategyRequest(BaseModel):
    """Request for rollout strategy suggestion."""

    flag_key: str = Field(...)
    target_audience: Optional[str] = None
    percentage: Optional[int] = Field(default=None, ge=0, le=100)


@router.get("/status/public")
def ai_status_public():
    """Public endpoint: whether AI/LLM is configured (no auth required)."""
    return {"ai_available": is_llm_available(), "features": ["summarize", "risk", "nl_query", "suggestions", "anomaly"]}


@router.get("/status")
def ai_status(user: dict = Depends(get_current_user)):
    """Return whether AI features are available (OpenAI configured)."""
    return {"ai_available": is_llm_available(), "features": ["summarize", "risk", "nl_query", "suggestions", "anomaly"]}


@router.get("/summarize/change-requests")
def summarize_change_requests_endpoint(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get an AI-generated summary of recent change requests."""
    items = (
        db.query(ChangeRequest)
        .order_by(ChangeRequest.created_at.desc())
        .limit(limit)
        .all()
    )
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
    summary = summarize_change_requests(data)
    return {"summary": summary, "count": len(data)}


@router.get("/summarize/audit")
def summarize_audit_endpoint(
    limit: int = Query(25, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get an AI-generated summary of recent audit log entries."""
    entries = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    data = [
        {
            "action": e.action,
            "actor": e.actor,
            "resource_type": e.resource_type,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]
    summary = summarize_audit_logs(data)
    return {"summary": summary, "count": len(data)}


@router.get("/risk/{change_request_id}")
def risk_score(
    change_request_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get risk score and optional AI explanation for a change request."""
    cr = db.query(ChangeRequest).filter(ChangeRequest.id == change_request_id).first()
    if not cr:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change request not found")
    payload = {
        "flag_key": cr.flag_key,
        "desired_changes": cr.desired_changes,
        "environment": cr.environment,
        "strategies": cr.strategies,
    }
    return get_risk_score(payload)


@router.post("/nl-query")
def nl_query(
    body: NLQueryRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Interpret a natural language query and return suggested filters + optional summary."""
    from app.ai.nl_query import parse_relative_time, extract_actor_from_query
    time_range = parse_relative_time(body.query)
    actor = extract_actor_from_query(body.query)
    start = time_range[0] if time_range else None
    end = time_range[1] if time_range else None
    result = interpret_nl_query(body.query, actor, start, end)

    # Optionally run audit query with interpreted filters
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if start:
        q = q.filter(AuditLog.created_at >= start)
    if end:
        q = q.filter(AuditLog.created_at <= end)
    if actor:
        q = q.filter(AuditLog.actor == actor)
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


@router.post("/suggest/flag-name", response_model=None)
def suggest_flag(
    body: SuggestFlagRequest,
    user: dict = Depends(get_current_user),
):
    """Suggest a feature flag key from a description."""
    return suggest_flag_name(body.description, body.project_context)


@router.post("/suggest/strategy", response_model=None)
def suggest_strategy(
    body: SuggestStrategyRequest,
    user: dict = Depends(get_current_user),
):
    """Suggest a rollout strategy for a flag."""
    return suggest_strategy_for_rollout(
        body.flag_key,
        body.target_audience,
        body.percentage,
    )


@router.get("/anomalies")
def anomalies(
    hours: int = Query(72, ge=6, le=168),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detect anomalies in audit/activity volume over the given period."""
    return detect_anomalies(db, hours=hours)


@router.get("/insights")
def insights(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregated AI insights: summary of change requests, audit summary, anomalies."""
    cr_items = (
        db.query(ChangeRequest)
        .order_by(ChangeRequest.created_at.desc())
        .limit(15)
        .all()
    )
    cr_data = [
        {
            "flag_key": cr.flag_key,
            "status": cr.status,
            "desired_changes": cr.desired_changes,
        }
        for cr in cr_items
    ]
    audit_entries = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
        .all()
    )
    audit_data = [
        {"action": e.action, "actor": e.actor, "resource_type": e.resource_type}
        for e in audit_entries
    ]
    return {
        "change_requests_summary": summarize_change_requests(cr_data),
        "audit_summary": summarize_audit_logs(audit_data),
        "anomalies": detect_anomalies(db, hours=72),
        "ai_available": is_llm_available(),
    }
