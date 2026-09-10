"""OpenAI-compatible hosted LLM client used by Codex.

Supports a primary endpoint (Funkash) with automatic fallback to a backup
endpoint (Ollama Cloud) on timeout or connection failure.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

import requests

from packages.shared.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_BACKUP_BASE_URL,
    LLM_BACKUP_API_KEY,
    LLM_BACKUP_MODEL,
)

logger = logging.getLogger(__name__)


class LLMClientError(RuntimeError):
    """Raised when the configured hosted LLM cannot answer a request."""


def _headers(api_key: str = "") -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _call_endpoint(
    base_url: str,
    api_key: str,
    model: str,
    payload: dict[str, Any],
    timeout: float,
    session: requests.Session,
) -> str:
    """Make a single chat completion call to an OpenAI-compatible endpoint."""
    response = session.post(
        f"{base_url}/chat/completions",
        json={**payload, "model": model},
        headers=_headers(api_key),
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    msg = body["choices"][0]["message"]
    content = msg.get("content") or ""
    # Some hosted APIs put output in 'reasoning' field
    if not content.strip() and msg.get("reasoning"):
        content = msg["reasoning"]
    return content.strip()


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
    """Call the primary hosted API with automatic fallback to backup.

    Tries the primary endpoint first. If it times out or the connection
    fails, retries on the backup endpoint. Both must fail for an error
    to be raised.
    """
    payload: dict[str, Any] = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if extra:
        payload.update(extra)

    client = session or requests.Session()

    # Try primary endpoint (Funkash)
    try:
        return _call_endpoint(LLM_BASE_URL, LLM_API_KEY, model, payload, timeout, client)
    except (requests.Timeout, requests.ConnectionError) as primary_err:
        # Primary failed with a transient network error — try backup
        if LLM_BACKUP_BASE_URL:
            backup_model = LLM_BACKUP_MODEL or model
            logger.warning(
                "Primary LLM endpoint (%s) unavailable: %s — falling back to %s",
                LLM_BASE_URL, primary_err, backup_model,
            )
            try:
                return _call_endpoint(
                    LLM_BACKUP_BASE_URL, LLM_BACKUP_API_KEY,
                    backup_model, payload, timeout, client,
                )
            except (requests.Timeout, requests.ConnectionError):
                # Both endpoints failed
                raise LLMClientError(
                    f"Both LLM endpoints failed. Primary: {primary_err}"
                ) from primary_err
            except LLMClientError:
                raise
        # No backup configured — re-raise primary error
        raise LLMClientError(str(primary_err)) from primary_err
    except LLMClientError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMClientError(f"Invalid hosted LLM response: {exc}") from exc
    except requests.RequestException as exc:
        # Non-transient error (e.g. 400, 401, 500) — try backup
        if LLM_BACKUP_BASE_URL:
            backup_model = LLM_BACKUP_MODEL or model
            logger.warning(
                "Primary LLM endpoint (%s) error: %s — falling back to %s",
                LLM_BASE_URL, exc, backup_model,
            )
            try:
                return _call_endpoint(
                    LLM_BACKUP_BASE_URL, LLM_BACKUP_API_KEY,
                    backup_model, payload, timeout, client,
                )
            except LLMClientError:
                raise
            except requests.RequestException:
                raise LLMClientError(
                    f"Both LLM endpoints failed. Primary: {exc}"
                ) from exc
        raise LLMClientError(str(exc)) from exc


def health_check(timeout: float = 5.0, session: Optional[requests.Session] = None) -> bool:
    """Return whether the hosted API responds successfully to /models."""
    client = session or requests.Session()
    try:
        response = client.get(
            f"{LLM_BASE_URL}/models",
            headers=_headers(LLM_API_KEY),
            timeout=timeout,
        )
        return response.ok
    except (LLMClientError, requests.RequestException):
        return False
