"""Risk scoring: deterministic heuristic + optional LLM explanation via JSON mode."""

from typing import Any

from app.ai.llm import complete_json, is_llm_available
from app.ai.prompts import RISK_JSON_INSTRUCTIONS


def _heuristic_risk_score(change_request: dict[str, Any]) -> float:
    """0-1 risk score from change-request content. Higher = riskier."""
    score = 0.0
    desired = change_request.get("desired_changes") or {}
    strategies = change_request.get("strategies") or []

    if desired.get("enabled") is True:
        score += 0.3
    if desired.get("type") and str(desired.get("type")).lower() == "release":
        score += 0.1
    if strategies:
        score += min(0.3, 0.1 * len(strategies))
    if change_request.get("environment") and "prod" in str(change_request["environment"]).lower():
        score += 0.2
    return min(1.0, score)


def _level(score: float) -> str:
    return "high" if score >= 0.6 else "medium" if score >= 0.3 else "low"


async def get_risk_score(change_request: dict[str, Any]) -> dict[str, Any]:
    """Return risk score, deterministic level, optional LLM concerns."""
    score = _heuristic_risk_score(change_request)
    out: dict[str, Any] = {
        "score": round(score, 2),
        "level": _level(score),
        "explanation": None,
        "concerns": [],
        "source": "heuristic",
    }

    if not is_llm_available():
        return out

    prompt = (
        f"Flag: {change_request.get('flag_key', '?')}\n"
        f"Environment: {change_request.get('environment', 'default')}\n"
        f"Desired changes: {change_request.get('desired_changes', {})}\n"
        f"Strategies: {change_request.get('strategies', [])}\n\n"
        f"{RISK_JSON_INSTRUCTIONS}"
    )
    result, parsed = await complete_json(prompt, feature="risk_explain", max_output_tokens=200)
    if not result.ok or not parsed:
        return out

    explanation = parsed.get("explanation")
    concerns = parsed.get("concerns") or []
    if isinstance(explanation, str):
        out["explanation"] = explanation.strip()
    if isinstance(concerns, list):
        out["concerns"] = [str(c) for c in concerns if isinstance(c, (str, int, float))][:5]
    out["source"] = "heuristic+llm"
    return out
