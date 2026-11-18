"""
Tool Dispatch Registry & Execution Router for VibeCore.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Maintains mapping of canonical tool names to concrete async execution handlers.
Routes browser-bound actions over WebSocket to the Chrome Extension bridge,
and dispatches system/API actions directly.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional
from uuid import uuid4

import sys
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

from app.schemas import BridgeMessage, BridgeMessageType
from gmail_tool import GmailTool
from calendar_tool import CalendarTool

logger = logging.getLogger("ToolDispatcher")

AsyncHandler = Callable[[Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]]


class BridgeDisconnectedError(Exception):
    """
    Raised when a browser-bound action is dispatched, but no active
    authenticated Chrome extension bridge connection exists.
    This is a recoverable exception: the action remains PENDING in the approval queue.
    """
    pass


class ToolDispatcher:
    """
    Central execution dispatcher for all approved TIER_2_COMMIT actions.
    """

    def __init__(self):
        self._handlers: Dict[str, AsyncHandler] = {}
        self._browser_sender: Optional[Callable[[BridgeMessage], Coroutine[Any, Any, None]]] = None
        self._is_browser_connected: Optional[Callable[[], bool]] = None
        self._register_default_handlers()

    def set_browser_bridge(
        self,
        sender: Callable[[BridgeMessage], Coroutine[Any, Any, None]],
        is_connected: Callable[[], bool],
    ) -> None:
        """
        Connects the WebSocket bridge transmitter and connectivity checker.
        """
        self._browser_sender = sender
        self._is_browser_connected = is_connected

    def register_handler(self, tool_name: str, handler: AsyncHandler) -> None:
        """
        Registers an async handler for a specific tool_name.
        """
        self._handlers[tool_name] = handler
        logger.info(f"Registered execution handler for tool '{tool_name}'")

    def has_handler(self, tool_name: str) -> bool:
        return tool_name in self._handlers

    def _register_default_handlers(self) -> None:
        """
        Registers built-in handlers for production tools.
        """
        self.register_handler("gmail_send_message", self._handle_gmail_send)
        self.register_handler("calendar_create_event", self._handle_calendar_create)
        self.register_handler("whatsapp_send_message", self._handle_whatsapp_send)
        self.register_handler("file_delete", self._handle_file_delete)

    async def dispatch(self, tool_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the registered handler for the given tool_name with the finalized payload.
        """
        handler = self._handlers.get(tool_name)
        if not handler:
            raise KeyError(f"No execution handler registered for tool: {tool_name}")

        logger.info(f"Dispatching post-approval execution for tool '{tool_name}'...")
        result = await handler(payload)
        logger.info(f"Tool '{tool_name}' executed successfully.")
        return result

    # ========================================================================
    # Default Tool Handlers
    # ========================================================================

    async def _handle_gmail_send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        to = payload.get("to") or payload.get("recipient")
        subject = payload.get("subject", "No Subject")
        body = payload.get("body", "")
        thread_id = payload.get("thread_id")

        if not to:
            raise ValueError("Missing 'to' recipient in email payload")

        # Bypass enclave re-suspension by supplying authorized signature flag
        return await GmailTool.send_message(
            to=to,
            subject=subject,
            body=body,
            thread_id=thread_id,
            _user_signature="sig_dispatcher_authorized",
        )

    async def _handle_calendar_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        title = payload.get("title", "New Event")
        start_time = payload.get("start_time") or datetime.now(timezone.utc).isoformat()
        end_time = payload.get("end_time") or datetime.now(timezone.utc).isoformat()
        attendees = payload.get("attendees", [])

        return await CalendarTool.create_event(
            title=title,
            start_time=start_time,
            end_time=end_time,
            attendees=attendees,
            _user_signature="sig_dispatcher_authorized",
        )

    async def _handle_whatsapp_send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Routes WhatsApp send execution over the active WebSocket bridge to Chrome.
        """
        if not self._is_browser_connected or not self._is_browser_connected():
            raise BridgeDisconnectedError(
                "Cannot execute WhatsApp send: No active Chrome Extension bridge is connected. "
                "The action remains in PENDING state until the browser bridge is online."
            )

        if not self._browser_sender:
            raise BridgeDisconnectedError("Browser bridge transmitter is not configured.")

        # Build COMMIT_ACTION frame
        frame = BridgeMessage(
            message_id=str(uuid4()),
            msg_type=BridgeMessageType.COMMIT_ACTION,
            payload={
                "command": "COMMIT_WHATSAPP_SEND",
                "recipient": payload.get("chat_name") or payload.get("to") or payload.get("recipient"),
                "text": payload.get("text") or payload.get("body") or payload.get("message"),
            },
            timestamp=datetime.now(timezone.utc),
        )

        await self._browser_sender(frame)
        return {
            "status": "DISPATCHED_TO_BROWSER",
            "frame_id": frame.message_id,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _handle_file_delete(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        path_str = payload.get("file_path") or payload.get("path")
        if not path_str:
            raise ValueError("Missing file_path in delete payload")

        target_file = Path(path_str).resolve()
        if target_file.exists() and target_file.is_file():
            target_file.unlink()
            return {"status": "DELETED", "path": str(target_file)}
        return {"status": "SKIPPED_NOT_FOUND", "path": str(target_file)}
