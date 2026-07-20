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

# Model constants
DEEPSEEK_MODEL = "deepseek-r1-distill-llama-8b"
PHI_MODEL = "Phi-4-mini-instruct-Q4_K_M.gguf"


@tool(name="llm_generate", failure_threshold=3)
def llm_generate(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout: float = 120.0,
) -> ToolResult:
    """
    Chat completion via llama.cpp with pooled HTTP session.
    
    Args:
        model: Model name (e.g., "deepseek-r1-distill-llama-8b")
        system_prompt: System message
        user_message: User message
        temperature: Sampling temperature (0.0 = deterministic)
        max_tokens: Maximum tokens to generate
        timeout: Request timeout in seconds
        
    Returns:
        ToolResult with data = generated text string
        
    Used by:
        - reasoner.py: DeepSeek-R1 (timeout=120s)
        - conflict_agent.py: Phi-4-mini (timeout=15s)
        - kg_extractor.py: Phi-4-mini (timeout=30s)
        - clause_detector.py: Phi-4-mini (timeout=30s)
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
    try:
        session = ConnectionPool.get_http()
        resp = session.post(
            "http://localhost:8080/v1/chat/completions",
            json=payload,
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
