"""Golden evals for NL-query regex parsing.

These are pinned expectations — deterministic (no LLM needed) — so CI catches drift
when the regex layer is refactored. Expand this list before shipping prompt changes.
"""

from datetime import datetime, timezone

import pytest

from app.ai.nl_query import extract_actor_from_query, parse_relative_time


GOLDEN_TIME = [
    ("events in the last 7 days", True, "days"),
    ("show me activity from the past 24 days", True, "days"),
    ("last 6 hours of audit", True, "hours"),
    ("what happened yesterday", True, "yesterday"),
    ("today's events", True, "today"),
    ("all activity ever", False, None),
]


GOLDEN_ACTOR = [
    ("events by alice@example.com in last 7 days", "alice@example.com"),
    ("changes by bob.k+governance@corp.io", "bob.k+governance@corp.io"),
    ("what did user 'carol' do", "carol"),
    ("no actor mention here", None),
]


@pytest.mark.parametrize("query,should_match,hint", GOLDEN_TIME)
def test_relative_time_golden(query: str, should_match: bool, hint):
    result = parse_relative_time(query)
    if should_match:
        assert result is not None, f"expected match for: {query}"
        start, end = result
        assert start.tzinfo is timezone.utc
        assert start <= end
        assert start <= datetime.now(timezone.utc)
    else:
        assert result is None, f"expected no match for: {query}"


@pytest.mark.parametrize("query,expected", GOLDEN_ACTOR)
def test_actor_extraction_golden(query: str, expected):
    assert extract_actor_from_query(query) == expected
