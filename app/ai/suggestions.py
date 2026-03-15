"""AI-powered suggestions for feature flag naming and grouping."""

import re
from typing import Any, Optional

from app.ai.llm import complete, is_llm_available


# Conventional patterns for flag keys
KEY_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def suggest_flag_name(description: str, project_context: Optional[str] = None) -> dict[str, Any]:
    """
    Suggest a feature flag key from a short description.
    Uses LLM when available; otherwise a slugified heuristic.
    """
    description = (description or "").strip()
    if not description:
        return {"suggested_key": "new_feature", "source": "default"}

    if is_llm_available():
        prompt = (
            "Suggest a single feature flag key (snake_case, no spaces, e.g. enable_dark_mode) "
            "for this description. Reply with ONLY the key, nothing else.\n\n"
            f"Description: {description}\n"
            + (f"Project context: {project_context}\n" if project_context else "")
        )
        out = complete(prompt, max_tokens=50, temperature=0.2)
        if out:
            key = out.strip().split()[0] if out.strip() else ""
            key = re.sub(r"[^a-zA-Z0-9_]", "_", key).strip("_") or "new_feature"
            if KEY_PATTERN.match(key):
                return {"suggested_key": key, "source": "llm", "description": description}
    # Heuristic: slugify
    key = description.lower()
    key = re.sub(r"[^a-z0-9\s]", " ", key)
    key = "_".join(key.split())[:64] or "new_feature"
    if not KEY_PATTERN.match(key):
        key = "new_feature"
    return {"suggested_key": key, "source": "heuristic", "description": description}


def suggest_strategy_for_rollout(
    flag_key: str,
    target_audience: Optional[str] = None,
    percentage: Optional[int] = None,
) -> dict[str, Any]:
    """
    Suggest a rollout strategy (e.g. gradual, canary) based on flag and context.
    """
    if is_llm_available():
        prompt = (
            f"Feature flag: {flag_key}. "
            + (f"Target: {target_audience}. " if target_audience else "")
            + (f"Desired percentage: {percentage}%. " if percentage is not None else "")
            + "Suggest in one sentence: gradual rollout, canary, or full rollout and why."
        )
        out = complete(prompt, max_tokens=120)
        if out:
            return {
                "suggestion": out.strip(),
                "source": "llm",
                "flag_key": flag_key,
            }
    return {
        "suggestion": "Use gradual rollout with percentage constraints for safer releases.",
        "source": "heuristic",
        "flag_key": flag_key,
    }
