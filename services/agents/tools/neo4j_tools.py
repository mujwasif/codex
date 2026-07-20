"""
Neo4j Tools

Unified Neo4j query tool with connection pooling and retry.
Replaces the per-call driver pattern in approval_agent, conflict_agent, and risk_agent.
"""

import time
from typing import Any, Dict, List, Optional
from services.agents.tools.base import ToolResult, tool
from services.agents.tools.connections import ConnectionPool


@tool(name="neo4j_query", failure_threshold=3)
def neo4j_query(cypher: str, params: Optional[Dict[str, Any]] = None) -> ToolResult:
    """
    Execute a Cypher query against Neo4j with pooled connection.
    
    Args:
        cypher: Cypher query string with $param placeholders
        params: Dict of query parameters
        
    Returns:
        ToolResult with data = list of record dicts
        
    Used by:
        - approval_agent: MATCH (r:Role)-[:CAN_APPROVE]->(p:Process)
        - conflict_agent: MATCH (c:Clause)-[:CONFLICTS_WITH]->(other:Clause)
        - risk_agent: MATCH (p:Policy)-[:MAPS_TO]->(reg:Regulation)
        - risk_agent: MATCH (c:Clause) RETURN c.obligation
    """
    start = time.time()
    try:
        driver = ConnectionPool.get_neo4j()
        records, _, _ = driver.execute_query(cypher, params or {})
        data = [dict(r) for r in records]
        return ToolResult(
            success=True,
            data=data,
            latency_ms=int((time.time() - start) * 1000),
            tool_name="neo4j_query"
        )
    except Exception as e:
        return ToolResult(
            success=False,
            error=str(e),
            latency_ms=int((time.time() - start) * 1000),
            tool_name="neo4j_query"
        )
