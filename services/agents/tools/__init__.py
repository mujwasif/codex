"""
Tool Calling System

Unified tool interface for all Codex agents.
Provides connection pooling, retry logic, circuit breakers, and latency tracking.

Usage:
    from services.agents.tools import neo4j_query, llm_generate, ConnectionPool

    # Initialize at startup
    ConnectionPool.initialize()

    # Use tools
    result = neo4j_query(cypher="MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c")
    if result.success:
        print(result.data)

    result = llm_generate(model="Qwen3-8B-Q4_K_M.gguf", system_prompt="...", user_message="...")
    if result.success:
        print(result.data)
"""

from services.agents.tools.base import (
    ToolResult,
    ToolCall,
    CircuitBreaker,
    ToolRegistry,
    tool,
    get_registry,
)
from services.agents.tools.connections import ConnectionPool
from services.agents.tools.neo4j_tools import neo4j_query
from services.agents.tools.llm_tools import (
    llm_generate,
    llm_generate_json,
    AGENT_MODEL,
    INGESTION_MODEL,
)
from services.agents.tools.catalog import ToolSelection, select_tools, TOOL_NAMES

__all__ = [
    "ToolResult",
    "ToolCall",
    "CircuitBreaker",
    "ToolRegistry",
    "tool",
    "get_registry",
    "ConnectionPool",
    "neo4j_query",
    "llm_generate",
    "llm_generate_json",
    "AGENT_MODEL",
    "INGESTION_MODEL",
    "ToolSelection",
    "select_tools",
    "TOOL_NAMES",
]
