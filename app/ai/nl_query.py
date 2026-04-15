"""NL → structured filters for audit queries.

Strategy: regex parses the common, deterministic cases (last N days, today, by user X).
When the LLM is available we additionally ask it for structured filters in JSON mode and
merge — LLM results fill gaps that regex missed, never overwrite explicit regex hits.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.ai.llm import complete_json, is_llm_available
from app.ai.prompts import NL_QUERY_JSON_INSTRUCTIONS

RELATIVE_PATTERNS = [
    (re.compile(r"last\s+(\d+)\s*(day|days)", re.I), "days"),
    (re.compile(r"past\s+(\d+)\s*(day|days)", re.I), "days"),
    (re.compile(r"last\s+(\d+)\s*(week|weeks)", re.I), "weeks"),
    (re.compile(r"last\s+(\d+)\s*(hour|hours)", re.I), "hours"),
    (re.compile(r"today", re.I), "today"),
    (re.compile(r"yesterday", re.I), "yesterday"),
]


def parse_relative_time(query: str) -> Optional[tuple[datetime, datetime]]:
    now = datetime.now(timezone.utc)
    for pattern, unit in RELATIVE_PATTERNS:
        m = pattern.search(query)
        if not m:
            continue
        if unit == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return (start, now)
        if unit == "yesterday":
            start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return (start, start + timedelta(days=1))
        n = int(m.group(1))
        if unit == "days":
            return (now - timedelta(days=n), now)
        if unit == "weeks":
            return (now - timedelta(weeks=n), now)
        if unit == "hours":
            return (now - timedelta(hours=n), now)
    return None


def extract_actor_from_query(query: str) -> Optional[str]:
    m = re.search(r"\bby\s+([^\s,]+@[^\s,]+)", query, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"user\s+['\"]?([^'\"]+)['\"]?", query, re.I)
    if m:
        return m.group(1).strip()
    return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def interpret_nl_query(
    query: str,
    db_actor_filter: Optional[str],
    db_start: Optional[datetime],
    db_end: Optional[datetime],
) -> dict[str, Any]:
    """Return structured filters. LLM fills gaps; regex wins when it matched."""
    time_range = parse_relative_time(query)
    actor = extract_actor_from_query(query) or db_actor_filter
    start = time_range[0] if time_range else db_start
    end = time_range[1] if time_range else db_end
    action: Optional[str] = None
    resource_type: Optional[str] = None
    intent: Optional[str] = None
    source = "regex"

    if is_llm_available() and query:
        prompt = (
            f"Current UTC time: {datetime.now(timezone.utc).isoformat()}\n"
            f"Question: {query}\n\n{NL_QUERY_JSON_INSTRUCTIONS}"
        )
        result, parsed = await complete_json(prompt, feature="nl_query", max_output_tokens=200)
        if result.ok and parsed:
            source = "regex+llm"
            if not actor and isinstance(parsed.get("actor"), str):
                actor = parsed["actor"]
            if not start:
                start = _parse_iso(parsed.get("start_iso"))
            if not end:
                end = _parse_iso(parsed.get("end_iso"))
            if isinstance(parsed.get("action"), str):
                action = parsed["action"]
            if isinstance(parsed.get("resource_type"), str):
                resource_type = parsed["resource_type"]
            if isinstance(parsed.get("intent"), str):
                intent = parsed["intent"]

    return {
        "interpreted": {
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "intent": intent,
            "source": source,
        },
        "query": query,
    }
