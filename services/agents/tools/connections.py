"""
Connection Pool

Manages pooled connections for Neo4j and HTTP (llama.cpp).
Created once at startup, shared across all agents.
"""

import requests
from neo4j import GraphDatabase


class ConnectionPool:
    """
    Singleton connection pool for external services.
    
    Initialize at FastAPI startup:
        ConnectionPool.initialize()
    
    Close at FastAPI shutdown:
        ConnectionPool.close()
    """
    _neo4j_driver = None
    _http_session = None
    _initialized = False

    @classmethod
    def initialize(cls, neo4j_uri: str = "neo4j://localhost:7687"):
        """Create pooled connections. Call once at startup."""
        if cls._initialized:
            return

        cls._neo4j_driver = GraphDatabase.driver(neo4j_uri)
        cls._http_session = requests.Session()
        cls._initialized = True
        print("✅ ConnectionPool initialized")

    @classmethod
    def get_neo4j(cls):
        """Get the pooled Neo4j driver."""
        if not cls._initialized:
            cls.initialize()
        return cls._neo4j_driver

    @classmethod
    def get_http(cls):
        """Get the pooled HTTP session (with keep-alive)."""
        if not cls._initialized:
            cls.initialize()
        return cls._http_session

    @classmethod
    def close(cls):
        """Close all pooled connections. Call at shutdown."""
        if cls._neo4j_driver:
            cls._neo4j_driver.close()
            cls._neo4j_driver = None
        if cls._http_session:
            cls._http_session.close()
            cls._http_session = None
        cls._initialized = False
        print("✅ ConnectionPool closed")
