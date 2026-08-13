"""
Base interface for chat integrations (Teams / Slack).
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class ChatIntegration(ABC):
    """Abstract inbound chat webhook integration.

    Implementations receive an inbound webhook payload, route it to the
    Codex query pipeline, and post the answer back to the chat platform.
    """

    platform: str = ""

    @abstractmethod
    def handle(self, payload: Dict[str, Any], headers: Dict[str, str] = None) -> Dict[str, Any]:
        """Process an inbound webhook payload.

        Returns a response dict suitable for the HTTP response body
        (e.g. {"challenge": "..."} for Slack URL verification).
        """
        raise NotImplementedError
