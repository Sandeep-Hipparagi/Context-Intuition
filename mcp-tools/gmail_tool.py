"""
Gmail MCP Tool wrapper with Zero-Trust Policy Enclave guardrails.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

from app.schemas import TierLevel
from enclave import enforce_policy
from google_workspace import workspace_connector


class GmailTool:
    """
    Gmail bridge enforcing TIER_0 for reading, TIER_1 for drafting,
    and TIER_2 for verified transmission.
    """

    @staticmethod
    @enforce_policy(tool_name="gmail_read_threads", tier=TierLevel.TIER_0_OBSERVE)
    async def read_unread_threads(max_results: int = 10, **kwargs: Any) -> list[dict[str, Any]]:
        """
        Reads recent unread threads for triage. Autonomous read-only.
        """
        return await workspace_connector.list_unread_threads(max_results=max_results)

    @staticmethod
    @enforce_policy(tool_name="gmail_create_draft", tier=TierLevel.TIER_1_STAGE)
    async def create_draft(
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        **kwargs: Any
    ) -> dict[str, Any]:
        """
        Creates a staged draft in Gmail. Autonomous staging.
        """
        return await workspace_connector.create_draft(
            to=to,
            subject=subject,
            body=body,
            thread_id=thread_id,
        )

    @staticmethod
    @enforce_policy(tool_name="gmail_send_message", tier=TierLevel.TIER_2_COMMIT)
    async def send_message(
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        **kwargs: Any
    ) -> dict[str, Any]:
        """
        Transmits email to external recipient. REQUIRES EXPLICIT USER APPROVAL.
        """
        return await workspace_connector.send_message(
            to=to,
            subject=subject,
            body=body,
            thread_id=thread_id,
        )
