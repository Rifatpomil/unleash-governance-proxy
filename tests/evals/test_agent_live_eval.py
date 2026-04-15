"""Live eval harness for the audit investigator agent.

Runs the real LLM against a golden Q&A set and scores tool-call behavior + answer
substrings. Gated behind `RUN_LIVE_EVALS=1` so normal CI stays fast, deterministic,
and offline. Intended to be run nightly (or on prompt changes) as a separate job.

Scoring is intentionally crude — exact substring matching — because the goal here
is *regression detection* on structured behavior (did the agent call the right
tool, did it return the right count), not open-ended text quality. Replace with
LLM-as-judge only if you add a budget and cache the judge.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from app.ai.agent import run_agent
from app.db.models import AuditLog, ChangeRequest

LIVE = os.getenv("RUN_LIVE_EVALS") == "1"
GOLDEN_PATH = Path(__file__).parent / "golden_agent_qa.yaml"


def _load_cases():
    with GOLDEN_PATH.open() as f:
        data = yaml.safe_load(f)
    return data["cases"]


def _seed(db, seed: dict) -> None:
    for row in seed.get("audit", []) or []:
        db.add(AuditLog(
            actor=row["actor"],
            action=row["action"],
            resource_type=row.get("resource_type", "change_request"),
        ))
    for row in seed.get("change_requests", []) or []:
        db.add(ChangeRequest(
            flag_key=row["flag_key"],
            project_id=row.get("project_id", "default"),
            desired_changes=row.get("desired_changes", {"description": "eval"}),
            status=row["status"],
            created_by=row["created_by"],
        ))
    db.commit()


@pytest.mark.skipif(not LIVE, reason="set RUN_LIVE_EVALS=1 and OPENAI_API_KEY to run")
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
async def test_live_eval_case(case, db_session):
    assert os.getenv("OPENAI_API_KEY"), "live evals require OPENAI_API_KEY"
    _seed(db_session, case.get("seed", {}))

    result = await run_agent(case["question"], db_session)

    assert result.ok, f"agent failed: {result.error}"
    tools_called = {s.tool for s in result.steps}

    required = set(case.get("required_tools", []))
    missing = required - tools_called
    assert not missing, f"missing required tools: {missing}; called: {tools_called}"

    forbidden = set(case.get("forbidden_tools", []))
    used_forbidden = tools_called & forbidden
    assert not used_forbidden, f"forbidden tools used: {used_forbidden}"

    answer = (result.answer or "").lower()
    for needle in case.get("answer_contains", []):
        assert needle.lower() in answer, f"answer missing {needle!r}: {result.answer!r}"
    for banned in case.get("answer_excludes", []):
        assert banned.lower() not in answer, f"answer contains banned {banned!r}: {result.answer!r}"

    rubric = case.get("judge_rubric")
    min_score = case.get("judge_min_score")
    if rubric and min_score is not None:
        from tests.evals.judge import judge_answer
        verdict = await judge_answer(case["question"], result.answer or "", rubric)
        assert verdict is not None, "judge LLM failed"
        assert verdict["score"] >= min_score, (
            f"judge score {verdict['score']} < {min_score}; reasons={verdict.get('reasons')}"
        )
