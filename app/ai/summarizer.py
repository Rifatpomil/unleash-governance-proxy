"""Summarization for change requests and audit logs. Async LLM + heuristic fallback."""

from typing import Any

from app.ai.llm import complete, is_llm_available


def _heuristic_cr_summary(requests: list[dict[str, Any]]) -> str:
    by_status: dict[str, int] = {}
    for r in requests:
        s = r.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    parts = [f"{c} {s}" for s, c in sorted(by_status.items(), key=lambda x: -x[1])]
    return f"Total: {len(requests)} change requests ({', '.join(parts)})."


def _heuristic_audit_summary(entries: list[dict[str, Any]]) -> str:
    by_action: dict[str, int] = {}
    for e in entries:
        a = e.get("action", "unknown")
        by_action[a] = by_action.get(a, 0) + 1
    parts = [f"{c} {a}" for a, c in sorted(by_action.items(), key=lambda x: -x[1])]
    return f"Total: {len(entries)} events ({', '.join(parts)})."


async def summarize_change_requests(requests: list[dict[str, Any]]) -> str:
    if not requests:
        return "No change requests."
    if not is_llm_available():
        return _heuristic_cr_summary(requests)
    lines = [
        f"- {r.get('flag_key', '?')} ({r.get('status', '?')}): "
        f"{str(r.get('desired_changes', {}))[:200]}"
        for r in requests[:20]
    ]
    prompt = (
        "Summarize these feature-flag change requests in 2-4 sentences. "
        "Focus on patterns, risk, and notable changes.\n\n" + "\n".join(lines)
    )
    result = await complete(prompt, feature="summarize_cr", max_output_tokens=300)
    return result.text or _heuristic_cr_summary(requests)


async def summarize_audit_logs(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No audit entries."
    if not is_llm_available():
        return _heuristic_audit_summary(entries)
    lines = [
        f"- {e.get('action', '?')} by {e.get('actor', '?')} on {e.get('resource_type', '?')} "
        f"at {e.get('created_at', '')}"
        for e in entries[:25]
    ]
    prompt = (
        "Summarize these governance audit entries in 2-4 sentences. "
        "Highlight who did what and notable patterns.\n\n" + "\n".join(lines)
    )
    result = await complete(prompt, feature="summarize_audit", max_output_tokens=300)
    return result.text or _heuristic_audit_summary(entries)
