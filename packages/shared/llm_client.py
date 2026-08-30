"""OpenAI-compatible hosted LLM client used by Codex."""

from __future__ import annotations

from typing import Any, Mapping, Optional

import requests

from packages.shared.config import LLM_API_KEY, LLM_BASE_URL


class LLMClientError(RuntimeError):
    """Raised when the configured hosted LLM cannot answer a request."""


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    return headers


def chat_completion(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: float = 120.0,
    session: Optional[requests.Session] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> str:
    """Call the configured hosted API and return assistant text."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if extra:
        payload.update(extra)

    client = session or requests.Session()
    try:
        response = client.post(
            f"{LLM_BASE_URL}/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        msg = body["choices"][0]["message"]
        content = msg.get("content") or ""
        # Hosted Qwen3 API puts output in 'reasoning' field, not 'content'
        if not content.strip() and msg.get("reasoning"):
            content = msg["reasoning"]
        return content.strip()
    except LLMClientError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMClientError(f"Invalid hosted LLM response: {exc}") from exc
    except requests.RequestException as exc:
        raise LLMClientError(str(exc)) from exc


def health_check(timeout: float = 5.0, session: Optional[requests.Session] = None) -> bool:
    """Return whether the hosted API responds successfully to /models."""
    client = session or requests.Session()
    try:
        response = client.get(
            f"{LLM_BASE_URL}/models",
            headers=_headers(),
            timeout=timeout,
        )
        return response.ok
    except (LLMClientError, requests.RequestException):
        return False
