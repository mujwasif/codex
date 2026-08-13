"""
Codex chat integrations.

Inbound chat webhooks (Teams / Slack) that let end users ask policy
questions through their messaging platform. Each integration exposes a
signed inbound endpoint registered on the FastAPI app as
POST /v1/integrations/{platform}.
"""

from integrations.base import ChatIntegration

__all__ = ["ChatIntegration"]
