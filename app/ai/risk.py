"""Risk scoring for change requests (heuristic + optional LLM enhancement)."""

from typing import Any, Optional


def _heuristic_risk_score(change_request: dict[str, Any]) -> float:
    """
    Compute a 0-1 risk score from change request content.
    Higher = riskier (e.g. enabling in prod, many strategy changes).
    """
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


def get_risk_score(change_request: dict[str, Any]) -> dict[str, Any]:
    """
    Return risk score and optional LLM explanation.
    """
    score = _heuristic_risk_score(change_request)
    explanation: Optional[str] = None

    from app.ai.llm import complete, is_llm_available
    if is_llm_available():
        prompt = (
            f"Feature flag change: {change_request.get('flag_key', '?')}. "
            f"Desired changes: {change_request.get('desired_changes', {})}. "
            f"Environment: {change_request.get('environment', 'default')}. "
            "In 1-2 sentences, what is the main risk or consideration?"
        )
        explanation = complete(prompt, max_tokens=150)
        if explanation:
            explanation = explanation.strip()

    return {
        "score": round(score, 2),
        "level": "high" if score >= 0.6 else "medium" if score >= 0.3 else "low",
        "explanation": explanation,
    }
