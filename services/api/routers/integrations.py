"""
Integration endpoints: signed inbound chat webhooks (Slack, Teams).
"""

import os

from fastapi import APIRouter, HTTPException, Request

from integrations.slack import SlackIntegration
from integrations.base import ChatIntegration

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])

# Module-level instances so one handler + token cache is shared across requests.
# A SlackIntegration with no signing secret runs in dev mode (signature skipped).
_slack = SlackIntegration()

# Teams is stubbed per spec (one integration for the MVP is Slack).
class _TeamsStub(ChatIntegration):
    platform = "teams"

    def handle(self, payload: dict, headers: dict = None) -> dict:
        raise NotImplementedError

_teams = _TeamsStub()


def _slack_integration() -> SlackIntegration:
    return _slack


@router.post("/slack")
async def slack_webhook(request: Request):
    """Slack Events API inbound webhook (signed)."""
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    integration = _slack_integration()

    if not integration.verify(raw, headers):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    return integration.handle(payload, headers)


@router.post("/teams")
async def teams_webhook():
    """Teams inbound webhook — not implemented for the MVP."""
    raise HTTPException(status_code=501, detail="Teams integration not implemented")
