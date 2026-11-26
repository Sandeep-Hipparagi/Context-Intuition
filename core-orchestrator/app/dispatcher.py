"""
Tool Dispatch Registry & Execution Router for VibeCore.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Maintains mapping of canonical tool names to concrete async execution handlers.
Routes browser-bound actions over WebSocket to the Chrome Extension bridge,
and dispatches system/API actions directly.
Enforces cryptographic capability token verification for all executions.
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
from app.config import settings
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


class CapabilityVerificationError(Exception):
    """
    Raised when a capability token is missing, invalid, expired, or already consumed.
    """
    pass


class ToolDispatcher:
    """
    Central execution dispatcher for all approved TIER_2_COMMIT actions.
    Enforces capability token verification for every execution.
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

    def _verify_capability_token(
        self,
        tool_name: str,
        payload: Dict[str, Any],
        capability_token: Optional[str],
        profile: str,
    ) -> None:
        """
        Verify capability token for execution authorization.
        Raises CapabilityVerificationError on failure.
        """
        if not capability_token:
            raise CapabilityVerificationError(
                f"Missing capability token for tool '{tool_name}'. "
                f"Tool execution requires a valid, unexpired capability token minted by PolicyEnclave."
            )

        # Import here to avoid circular imports
        from capabilities import verify_capability_token, CapabilityVerificationError as CapError

        try:
            verify_capability_token(capability_token, tool_name, payload, profile)
        except CapError as e:
            raise CapabilityVerificationError(f"Capability verification failed for '{tool_name}': {e}")

    async def dispatch(
        self,
        tool_name: str,
        payload: Dict[str, Any],
        envelope_id: Optional[str] = None,
        capability_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes the registered handler for the given tool_name with the finalized payload.
        Enforces zero-trust: all executions require a verified capability token.
        """
        from enclave import get_policy_enclave, ApprovalStatus, TierLevel

        enclave = get_policy_enclave()
        tier = enclave._policy_loader.get_tool_tier(tool_name)

        # For TIER_2 tools, require envelope_id and verify envelope status
        if tier == TierLevel.TIER_2_COMMIT:
            if not envelope_id:
                raise PermissionError(
                    f"Direct dispatch of TIER_2 tool '{tool_name}' rejected: Missing verified envelope_id."
                )
            envelope = enclave.queue.get(envelope_id)
            if not envelope:
                raise PermissionError(
                    f"Direct dispatch of TIER_2 tool '{tool_name}' rejected: ActionEnvelope '{envelope_id}' not found."
                )
            if envelope.status not in (ApprovalStatus.APPROVED, ApprovalStatus.EDITED, ApprovalStatus.EXECUTING):
                raise PermissionError(
                    f"Direct dispatch of TIER_2 tool '{tool_name}' rejected: "
                    f"ActionEnvelope '{envelope_id}' is in unauthorized status '{envelope.status.value}'."
                )

        # CRITICAL: Verify capability token for ALL executions (TIER_0, TIER_1, TIER_2)
        # This prevents unauthorized direct calls to dispatcher
        profile = settings.PROFILE
        self._verify_capability_token(tool_name, payload, capability_token, profile)

        handler = self._handlers.get(tool_name)
        if not handler:
            raise KeyError(f"No execution handler registered for tool: {tool_name}")

        logger.info(f"Dispatching post-approval execution for tool '{tool_name}' [envelope: {envelope_id}]...")
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

        from app.config import get_vibecore_home

        target_file = Path(path_str).resolve()
        vibecore_home = get_vibecore_home().resolve()

        # Get sandbox roots from policy (with fallback)
        from policy_loader import get_policy_loader
        policy = get_policy_loader().load()
        sandbox_roots = policy.sandbox_roots or ["staged_clips", "exports", "temp"]

        allowed_roots = [vibecore_home / root for root in sandbox_roots]
        allowed_roots.insert(0, vibecore_home)  # Also allow vibecore home root

        is_safe = False
        for root in allowed_roots:
            try:
                target_file.relative_to(root.resolve())
                is_safe = True
                break
            except ValueError:
                continue

        if not is_safe:
            raise PermissionError(
                f"Path traversal violation: Target '{target_file}' is outside authorized sandbox directories."
            )

        if target_file.exists() and target_file.is_file():
            target_file.unlink()
            return {"status": "DELETED", "path": str(target_file)}
        return {"status": "SKIPPED_NOT_FOUND", "path": str(target_file)}