"""LLM-as-judge scorer for the agent eval harness.

Substring matching catches regressions but not semantic drift ("Alice made 2
changes" vs "2 events by alice" vs "two"). The judge takes the question, the
agent's answer, and a reference rubric, and returns a structured verdict.

Design notes:
- The judge itself uses JSON mode so the score is machine-readable.
- We cache by (question, answer, rubric) hash on disk so re-runs don't repay
  the LLM bill. Delete `.judge_cache.json` to force a re-grade.
- The judge is deliberately small (temperature 0, terse system prompt) so its
  scores are reproducible within ~1 point.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from app.ai.llm import complete_json
from app.ai.prompts import JUDGE_JSON_INSTRUCTIONS, JUDGE_SYSTEM

_CACHE_PATH = Path(__file__).parent / ".judge_cache.json"


def _cache_key(question: str, answer: str, rubric: str) -> str:
    h = hashlib.sha256()
    h.update(question.encode())
    h.update(b"|")
    h.update(answer.encode())
    h.update(b"|")
    h.update(rubric.encode())
    return h.hexdigest()


def _load_cache() -> dict[str, Any]:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict[str, Any]) -> None:
    _CACHE_PATH.write_text(json.dumps(cache, indent=2))


async def judge_answer(
    question: str,
    answer: str,
    rubric: str,
    *,
    use_cache: bool = True,
) -> Optional[dict[str, Any]]:
    """Return {score, verdict, reasons} or None if the judge LLM errored."""
    key = _cache_key(question, answer, rubric)
    cache = _load_cache() if use_cache else {}
    if key in cache:
        return cache[key]

    prompt = (
        f"Question: {question}\n\n"
        f"Agent answer: {answer}\n\n"
        f"Rubric / ground truth: {rubric}\n\n"
        f"{JUDGE_JSON_INSTRUCTIONS}"
    )
    result, parsed = await complete_json(
        prompt, feature="judge", system=JUDGE_SYSTEM, max_output_tokens=200, temperature=0.0
    )
    if not result.ok or not parsed:
        return None

    if use_cache:
        cache[key] = parsed
        _save_cache(cache)
    return parsed
