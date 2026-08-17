"""
LLM Tools

Unified LLM generation tool with connection pooling and retry.
Replaces the per-call requests.post() pattern in reasoner and conflict_agent.
"""

import time
import re
import json
from typing import Optional
from services.agents.tools.base import ToolResult, tool
from services.agents.tools.connections import ConnectionPool
from packages.shared.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_INGESTION_MODEL,
    LLM_MODEL,
    LLM_PROVIDER,
    get_llm_provider,
    QWEN3_8B_MODEL,
    QWEN3_4B_MODEL,
)

AGENT_8081_MODELS = {QWEN3_4B_MODEL.lower()}


@tool(name="llm_generate", failure_threshold=3)
def llm_generate(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: float = 120.0,
    thinking_budget_tokens: Optional[int] = None,
    enable_thinking: Optional[bool] = None,
) -> ToolResult:
    """
    Chat completion via llama.cpp with pooled HTTP session.
    
    Args:
        model: Model name (e.g., "Qwen3-8B-Q4_K_M.gguf")
        system_prompt: System message
        user_message: User message
        temperature: Sampling temperature (0.0 = deterministic)
        max_tokens: Maximum tokens to generate
        timeout: Request timeout in seconds
        thinking_budget_tokens: Per-request reasoning token budget; 0 disables
            thinking for this call (server default stays unchanged).
        enable_thinking: Qwen3 per-request toggle; False disables thinking for
            this call via chat_template_kwargs, True forces it on.
        
    Returns:
        ToolResult with data = generated text string
        
    Used by:
        - reasoner.py: Qwen3-8B QA (thinking on, timeout=120s)
        - orchestrator.py: Qwen3-8B intent classification (thinking off)
        - risk_agent.py: Qwen3-8B compliance verdict (thinking off)
        - conflict_agent.py: Qwen3-8B conflict check (thinking off)
        - kg_extractor.py: Qwen (timeout=30s)
        - clause_detector.py: Qwen (timeout=30s)
    """
    start = time.time()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False
    }
    if thinking_budget_tokens is not None:
        payload["thinking_budget_tokens"] = thinking_budget_tokens
    if enable_thinking is not None:
        payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
    try:
        session = ConnectionPool.get_http()
        from packages.shared.config import LLAMA_8B_URL, LLAMA_4B_URL
        provider = get_llm_provider()
        if provider == "api":
            if not LLM_API_KEY:
                return ToolResult(success=False, error="LLM_API_KEY is not configured", tool_name="llm_generate")
            base_url = LLM_BASE_URL
            request_model = LLM_INGESTION_MODEL if model.lower() in AGENT_8081_MODELS else LLM_MODEL
            payload["model"] = request_model
            # These are llama.cpp/Qwen-specific and are rejected by most hosted APIs.
            payload.pop("thinking_budget_tokens", None)
            payload.pop("chat_template_kwargs", None)
            headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
        else:
            base_url = LLAMA_4B_URL if model.lower() in AGENT_8081_MODELS else LLAMA_8B_URL
            headers = {}
        endpoint = f"{base_url}/chat/completions" if provider == "api" else f"{base_url}/v1/chat/completions"
        resp = session.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=timeout
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        return ToolResult(
            success=True,
            data=content,
            latency_ms=int((time.time() - start) * 1000),
            tool_name="llm_generate"
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=str(e),
            latency_ms=int((time.time() - start) * 1000),
            tool_name="llm_generate"
        )


def llm_generate_json(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.0,
    max_tokens: int = 200,
    timeout: float = 30.0,
) -> ToolResult:
    """
    LLM generation with JSON extraction from response.
    Used by kg_extractor and clause_detector.
    """
    result = llm_generate(
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout
    )
    if not result.success:
        return result

    content = result.data
    json_match = re.search(r'\{.*\}', content, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            result.data = parsed
        except json.JSONDecodeError:
            result.error = "Failed to parse JSON from LLM response"
            result.success = False

    return result
