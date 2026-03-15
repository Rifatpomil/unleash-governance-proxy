"""Natural language query parsing for audit and change requests."""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.ai.llm import complete, is_llm_available


# Relative time patterns
RELATIVE_PATTERNS = [
    (re.compile(r"last\s+(\d+)\s*(day|days)", re.I), "days"),
    (re.compile(r"past\s+(\d+)\s*(day|days)", re.I), "days"),
    (re.compile(r"last\s+(\d+)\s*(week|weeks)", re.I), "weeks"),
    (re.compile(r"last\s+(\d+)\s*(hour|hours)", re.I), "hours"),
    (re.compile(r"today", re.I), "today"),
    (re.compile(r"yesterday", re.I), "yesterday"),
]


def parse_relative_time(query: str) -> Optional[tuple[datetime, datetime]]:
    """
    Parse relative time from natural language (e.g. "last 7 days").
    Returns (start_utc, end_utc) or None.
    """
    now = datetime.now(timezone.utc)
    query = query.strip()

    for pattern, unit in RELATIVE_PATTERNS:
        m = pattern.search(query)
        if m:
            if unit == "today":
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                return (start, now)
            if unit == "yesterday":
                start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)
                return (start, end)
            n = int(m.group(1))
            if unit == "days":
                start = now - timedelta(days=n)
                return (start, now)
            if unit == "weeks":
                start = now - timedelta(weeks=n)
                return (start, now)
            if unit == "hours":
                start = now - timedelta(hours=n)
                return (start, now)
    return None


def extract_actor_from_query(query: str) -> Optional[str]:
    """Simple heuristic: look for 'by user@email' or 'user X'."""
    by_match = re.search(r"\bby\s+([^\s,]+@[^\s,]+)", query, re.I)
    if by_match:
        return by_match.group(1).strip()
    user_match = re.search(r"user\s+['\"]?([^'\"]+)['\"]?", query, re.I)
    if user_match:
        return user_match.group(1).strip()
    return None


def interpret_nl_query(
    query: str,
    db_actor_filter: Optional[str],
    db_start: Optional[datetime],
    db_end: Optional[datetime],
) -> dict[str, Any]:
    """
    Interpret natural language and return structured filters + optional LLM summary.
    """
    time_range = parse_relative_time(query)
    actor = extract_actor_from_query(query) or db_actor_filter
    start = db_start
    end = db_end
    if time_range:
        start, end = time_range

    result = {
        "interpreted": {
            "actor": actor,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        },
        "query": query,
    }

    if is_llm_available() and query:
        prompt = (
            f"User asked: \"{query}\". "
            "In one short sentence, state what data we will show (e.g. 'Audit events from the last 7 days')."
        )
        summary = complete(prompt, max_tokens=80)
        if summary:
            result["interpreted"]["summary"] = summary.strip()
    return result
