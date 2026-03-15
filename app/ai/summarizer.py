"""AI-powered summarization for change requests and audit logs."""

from typing import Any, Optional

from app.ai.llm import complete, is_llm_available


def summarize_change_requests(requests: list[dict[str, Any]]) -> str:
    """
    Produce a short summary of a list of change requests.
    Uses LLM when available, otherwise a heuristic summary.
    """
    if not requests:
        return "No change requests."
    if is_llm_available():
        lines = []
        for r in requests[:20]:
            lines.append(
                f"- {r.get('flag_key', '?')} ({r.get('status', '?')}): "
                f"{str(r.get('desired_changes', {}))[:200]}"
            )
        prompt = (
            "Summarize these feature flag change requests in 2-4 concise sentences. "
            "Focus on patterns, risk, and notable changes.\n\n" + "\n".join(lines)
        )
        out = complete(prompt, max_tokens=300)
        if out:
            return out.strip()
    # Heuristic fallback
    by_status = {}
    for r in requests:
        s = r.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    parts = [f"{c} {s}" for s, c in sorted(by_status.items(), key=lambda x: -x[1])]
    return f"Total: {len(requests)} change requests ({', '.join(parts)})."


def summarize_audit_logs(entries: list[dict[str, Any]]) -> str:
    """
    Summarize audit log entries. Uses LLM when available.
    """
    if not entries:
        return "No audit entries."
    if is_llm_available():
        lines = []
        for e in entries[:25]:
            lines.append(
                f"- {e.get('action', '?')} by {e.get('actor', '?')} on {e.get('resource_type', '?')} "
                f"at {e.get('created_at', '')}"
            )
        prompt = (
            "Summarize these governance audit log entries in 2-4 sentences. "
            "Highlight who did what and any notable patterns.\n\n" + "\n".join(lines)
        )
        out = complete(prompt, max_tokens=300)
        if out:
            return out.strip()
    by_action = {}
    for e in entries:
        a = e.get("action", "unknown")
        by_action[a] = by_action.get(a, 0) + 1
    parts = [f"{c} {a}" for a, c in sorted(by_action.items(), key=lambda x: -x[1])]
    return f"Total: {len(entries)} events ({', '.join(parts)})."
