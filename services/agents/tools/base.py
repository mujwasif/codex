"""
Tool Calling Base Types

Core dataclasses and registry for the tool calling system.
Provides ToolResult, ToolCall, CircuitBreaker, and ToolRegistry.
"""

import time
import functools
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class ToolResult:
    """Result returned by every tool call."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    latency_ms: int = 0
    tool_name: str = ""


@dataclass
class ToolCall:
    """Tool call request."""
    tool_name: str
    params: dict
    timeout: float = 30.0
    retries: int = 2
    fallback: Any = None


@dataclass
class CircuitBreaker:
    """
    Prevents cascading failures by stopping calls to failing services.
    
    States:
        CLOSED: Normal operation, calls go through
        OPEN: Service failing, calls return fallback immediately
        HALF_OPEN: Testing if service recovered
    """
    failure_threshold: int = 3
    recovery_timeout: float = 60.0
    state: str = "closed"
    failure_count: int = 0
    last_failure_time: float = 0.0

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"

    def should_allow(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = "half_open"
                return True
            return False
        if self.state == "half_open":
            return True
        return False


class ToolRegistry:
    """
    Singleton registry of all available tools.
    Manages circuit breakers and provides unified call interface.
    """
    _instance = None
    _tools: Dict[str, Callable] = {}
    _breakers: Dict[str, CircuitBreaker] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools = {}
            cls._instance._breakers = {}
        return cls._instance

    def register(self, name: str, fn: Callable, failure_threshold: int = 3):
        self._tools[name] = fn
        self._breakers[name] = CircuitBreaker(failure_threshold=failure_threshold)

    def call(self, tool_name: str, retries: int = 2, backoff: float = 0.5, **params) -> ToolResult:
        """
        Execute a tool with circuit breaker + retry logic.
        
        Args:
            tool_name: Name of registered tool
            retries: Number of retry attempts
            backoff: Base backoff delay (doubles each retry)
            **params: Tool-specific parameters
            
        Returns:
            ToolResult with success/data/error/latency_ms
        """
        if tool_name not in self._tools:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' not registered",
                tool_name=tool_name
            )

        breaker = self._breakers[tool_name]
        fn = self._tools[tool_name]

        if not breaker.should_allow():
            return ToolResult(
                success=False,
                error=f"Circuit breaker OPEN for '{tool_name}'",
                tool_name=tool_name
            )

        last_result = None
        for attempt in range(retries + 1):
            start = time.time()
            try:
                result = fn(**params)
                result.latency_ms = int((time.time() - start) * 1000)
                result.tool_name = tool_name
                breaker.record_success()
                return result
            except Exception as e:
                last_result = ToolResult(
                    success=False,
                    error=str(e),
                    latency_ms=int((time.time() - start) * 1000),
                    tool_name=tool_name
                )
                breaker.record_failure()
                if attempt < retries:
                    time.sleep(backoff * (2 ** attempt))

        return last_result


def tool(fn: Optional[Callable] = None, *, name: Optional[str] = None, failure_threshold: int = 3):
    """
    Decorator to register a function as a tool.
    
    Usage:
        @tool
        def my_tool(param1: str) -> ToolResult:
            ...
        
        # Or with custom name:
        @tool(name="custom_name")
        def my_tool(param1: str) -> ToolResult:
            ...
    """
    def decorator(func):
        tool_name = name or func.__name__
        registry = ToolRegistry()
        registry.register(tool_name, func, failure_threshold)
        
        @functools.wraps(func)
        def wrapper(**params):
            return registry.call(tool_name, **params)
        
        wrapper._tool_name = tool_name
        wrapper._original = func
        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator


def get_registry() -> ToolRegistry:
    return ToolRegistry()
