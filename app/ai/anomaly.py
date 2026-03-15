"""Anomaly detection on governance metrics (request/audit volume)."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session


def _get_audit_counts_by_hour(db: Session, hours: int = 72) -> list[tuple[datetime, int]]:
    """Return (hour_bucket, count) for last N hours. Uses PostgreSQL date_trunc; on SQLite returns empty."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    from app.db.models import AuditLog
    try:
        rows = (
            db.query(
                func.date_trunc("hour", AuditLog.created_at).label("hour"),
                func.count(AuditLog.id).label("cnt"),
            )
            .filter(AuditLog.created_at >= since)
            .group_by(func.date_trunc("hour", AuditLog.created_at))
            .order_by("hour")
            .all()
        )
        return [(r.hour, r.cnt) for r in rows] if rows else []
    except Exception:
        return []


def _z_score(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.0
    return (value - mean) / std


def detect_anomalies(db: Session, hours: int = 72) -> dict[str, Any]:
    """
    Detect anomalous activity in audit volume (e.g. spike in changes).
    Returns recent counts, baseline stats, and flagged hours.
    """
    buckets = _get_audit_counts_by_hour(db, hours=hours)
    if len(buckets) < 3:
        return {
            "anomalies": [],
            "summary": "Insufficient data for anomaly detection.",
            "period_hours": hours,
            "data_points": len(buckets),
        }

    counts = [c for _, c in buckets]
    mean = sum(counts) / len(counts)
    variance = sum((x - mean) ** 2 for x in counts) / len(counts)
    std = variance ** 0.5
    anomalies = []
    for hour, cnt in buckets:
        z = _z_score(float(cnt), mean, std)
        if z > 2.0:
            anomalies.append({
                "hour": hour.isoformat() if hour else None,
                "count": cnt,
                "z_score": round(z, 2),
                "message": f"Unusual spike: {cnt} events (z={z:.2f})",
            })

    return {
        "anomalies": anomalies,
        "summary": f"Checked {len(buckets)} hours; {len(anomalies)} anomaly(ies) detected."
        if anomalies else "No significant anomalies in the period.",
        "period_hours": hours,
        "mean_events_per_hour": round(mean, 2),
        "std_events_per_hour": round(std, 2),
        "data_points": len(buckets),
    }
