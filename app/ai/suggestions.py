"""Flag-name and rollout-strategy suggestions. JSON mode + heuristic fallback."""

import re
from typing import Any, Optional

from app.ai.llm import complete, complete_json, is_llm_available
from app.ai.prompts import FLAG_NAME_JSON_INSTRUCTIONS

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _slugify(description: str) -> str:
    key = description.lower()
    key = re.sub(r"[^a-z0-9\s]", " ", key)
    key = "_".join(key.split())[:64] or "new_feature"
    return key if KEY_PATTERN.match(key) else "new_feature"


async def suggest_flag_name(description: str, project_context: Optional[str] = None) -> dict[str, Any]:
    description = (description or "").strip()
    if not description:
        return {"suggested_key": "new_feature", "source": "default"}

    if not is_llm_available():
        return {"suggested_key": _slugify(description), "source": "heuristic", "description": description}

    prompt = (
        f"Description: {description}\n"
        + (f"Project context: {project_context}\n" if project_context else "")
        + "\n" + FLAG_NAME_JSON_INSTRUCTIONS
    )
    result, parsed = await complete_json(prompt, feature="suggest_flag_name", max_output_tokens=80)
    if result.ok and parsed:
        key = str(parsed.get("key", "")).strip()
        if KEY_PATTERN.match(key):
            return {
                "suggested_key": key,
                "rationale": parsed.get("rationale"),
                "source": "llm",
                "description": description,
            }
    return {"suggested_key": _slugify(description), "source": "heuristic_fallback", "description": description}


async def suggest_strategy_for_rollout(
    flag_key: str,
    target_audience: Optional[str] = None,
    percentage: Optional[int] = None,
) -> dict[str, Any]:
    if is_llm_available():
        prompt = (
            f"Feature flag: {flag_key}. "
            + (f"Target: {target_audience}. " if target_audience else "")
            + (f"Desired percentage: {percentage}%. " if percentage is not None else "")
            + "In one sentence: recommend gradual rollout, canary, or full rollout and why."
        )
        result = await complete(prompt, feature="suggest_strategy", max_output_tokens=120)
        if result.ok and result.text:
            return {"suggestion": result.text, "source": "llm", "flag_key": flag_key}
    return {
        "suggestion": "Use gradual rollout with percentage constraints for safer releases.",
        "source": "heuristic",
        "flag_key": flag_key,
    }
