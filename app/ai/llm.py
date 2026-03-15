"""Optional LLM client for AI features. Graceful no-op when OPENAI_API_KEY is not set."""

from typing import Optional

from app.config import get_settings


def is_llm_available() -> bool:
    """Return True if OpenAI API key is configured and AI features are enabled."""
    s = get_settings()
    return bool(s.openai_api_key and s.ai_features_enabled)


def get_openai_client():
    """Return OpenAI client if available, else None. Lazy import to avoid hard dependency."""
    if not is_llm_available():
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=get_settings().openai_api_key)
    except Exception:
        return None


def complete(prompt: str, max_tokens: int = 500, temperature: float = 0.3) -> Optional[str]:
    """
    Call OpenAI chat completion. Returns None if LLM not configured or on error.
    """
    client = get_openai_client()
    if not client:
        return None
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if response.choices:
            return response.choices[0].message.content
    except Exception:
        pass
    return None
