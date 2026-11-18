"""
Google Calendar MCP Tool wrapper with Policy Guardrails.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

from enclave import enforce_policy
from app.schemas import TierLevel
from google_workspace import workspace_connector


class CalendarTool:
    """
    Manages schedule events, conflict audits, and proposed slots.
    """

    @staticmethod
    @enforce_policy(tool_name="calendar_list_events", tier=TierLevel.TIER_0_OBSERVE)
    async def list_events(window_hours: int = 72, **kwargs: Any) -> List[Dict[str, Any]]:
        return await workspace_connector.list_calendar_events(window_hours=window_hours)

    @staticmethod
    @enforce_policy(tool_name="calendar_create_event", tier=TierLevel.TIER_2_COMMIT)
    async def create_event(
        title: str,
        start_time: str,
        end_time: str,
        attendees: List[str],
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Modifies user's active calendar. TIER_2_COMMIT.
        """
        return await workspace_connector.create_event(
            title=title,
            start_time=start_time,
            end_time=end_time,
            attendees=attendees,
        )
