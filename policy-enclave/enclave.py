"""
VibeCore Zero-Trust Policy Enclave & Approval Queue.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Deterministic guardrails, risk scoring, action suspension, and edit-delta trigger hooks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import functools
import json
import logging
import os
import sqlite3
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple, TypeVar, cast
from uuid import uuid4

import sys
from pathlib import Path

# Add project root to path for cross-module imports
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

from app.config import get_db_path
from app.schemas import (
    ActionEnvelope,
    ApprovalStatus,
    DiffPreview,
    TierLevel,
)

logger = logging.getLogger("PolicyEnclave")
logging.basicConfig(level=logging.INFO)

F = TypeVar("F", bound=Callable[..., Any])


class ActionSuspendedException(Exception):
    """
    Raised when an action is classified as TIER_2_COMMIT and suspended
    pending explicit human-in-the-loop (HITL) authorization.
    """
    def __init__(self, envelope: ActionEnvelope):
        self.envelope = envelope
        super().__init__(
            f"Action {envelope.action_id} [{envelope.tool_name}] suspended. "
            f"Requires Tier-2 approval. Risk score: {envelope.risk_score:.2f}"
        )


class PolicyViolationException(Exception):
    """
    Raised when an agent attempts an explicitly prohibited operation.
    """
    pass


class IdempotencyConflictError(Exception):
    """
    Raised when an attempt is made to resolve an action that has already
    been resolved (i.e. status is not PENDING). Prevents duplicate executions.
    """
    pass


class ApprovalQueue:
    """
    Thread-safe and async-safe persistent queue for suspended actions.
    Stores pending envelopes in SQLite and notifies listeners via async futures.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(get_db_path("enclave_approvals.db"))
        self._pending_futures: Dict[str, asyncio.Future[ActionEnvelope]] = {}
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approval_queue (
                    action_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    risk_score REAL NOT NULL,
                    risk_factors TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    diff_preview TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    user_signature TEXT,
                    user_feedback TEXT,
                    execution_result TEXT
                )
            """)
            # Automatic schema migration for existing databases
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(approval_queue)")
            columns = [row[1] for row in cursor.fetchall()]
            if "execution_result" not in columns:
                conn.execute("ALTER TABLE approval_queue ADD COLUMN execution_result TEXT")
            conn.commit()

    def enqueue(self, envelope: ActionEnvelope) -> asyncio.Future[ActionEnvelope]:
        loop = asyncio.get_event_loop()
        future: asyncio.Future[ActionEnvelope] = loop.create_future()
        self._pending_futures[envelope.action_id] = future

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO approval_queue (
                    action_id, agent_id, session_id, tool_name, tier,
                    risk_score, risk_factors, payload, diff_preview,
                    status, created_at, resolved_at, user_signature, user_feedback, execution_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.action_id,
                    envelope.agent_id,
                    envelope.session_id,
                    envelope.tool_name,
                    envelope.tier.value,
                    envelope.risk_score,
                    json.dumps(envelope.risk_factors),
                    json.dumps(envelope.payload),
                    json.dumps(envelope.diff_preview.model_dump()) if envelope.diff_preview else None,
                    envelope.status.value,
                    envelope.created_at.isoformat(),
                    envelope.resolved_at.isoformat() if envelope.resolved_at else None,
                    envelope.user_signature,
                    envelope.user_feedback,
                    json.dumps(envelope.execution_result) if envelope.execution_result else None,
                ),
            )
            conn.commit()

        logger.info(
            f" [APPROVAL_QUEUE] Enqueued TIER_2 action {envelope.action_id} "
            f"for tool '{envelope.tool_name}' (Risk: {envelope.risk_score:.2f})"
        )
        return future

    def get(self, action_id: str) -> Optional[ActionEnvelope]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approval_queue WHERE action_id = ?", (action_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_envelope(row)

    def list_pending(self) -> List[ActionEnvelope]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approval_queue WHERE status = ? ORDER BY created_at DESC", (ApprovalStatus.PENDING.value,))
            rows = cursor.fetchall()
            return [self._row_to_envelope(row) for row in rows]

    def resolve(
        self,
        action_id: str,
        status: ApprovalStatus,
        user_signature: Optional[str] = None,
        edited_payload: Optional[Dict[str, Any]] = None,
        user_feedback: Optional[str] = None,
        execution_result: Optional[Dict[str, Any]] = None,
    ) -> ActionEnvelope:
        envelope = self.get(action_id)
        if not envelope:
            raise ValueError(f"Action {action_id} not found in approval queue.")

        # Strict idempotency check: only PENDING actions can be resolved
        if envelope.status != ApprovalStatus.PENDING:
            raise IdempotencyConflictError(
                f"Action {action_id} has already been resolved with status '{envelope.status.value}'. "
                f"Duplicate resolution rejected to enforce idempotency."
            )

        resolved_time = datetime.now(timezone.utc)
        final_payload = edited_payload if edited_payload is not None else envelope.payload

        envelope.status = status
        envelope.resolved_at = resolved_time
        envelope.user_signature = user_signature
        envelope.user_feedback = user_feedback
        envelope.payload = final_payload
        envelope.execution_result = execution_result

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE approval_queue
                SET status = ?, resolved_at = ?, user_signature = ?, user_feedback = ?, payload = ?, execution_result = ?
                WHERE action_id = ?
                """,
                (
                    status.value,
                    resolved_time.isoformat(),
                    user_signature,
                    user_feedback,
                    json.dumps(final_payload),
                    json.dumps(execution_result) if execution_result is not None else None,
                    action_id,
                ),
            )
            conn.commit()

        # Resolve async future if waiting in active process
        future = self._pending_futures.pop(action_id, None)
        if future and not future.done():
            future.set_result(envelope)

        logger.info(f" [APPROVAL_QUEUE] Action {action_id} marked as {status.value}")
        return envelope

    def _row_to_envelope(self, row: sqlite3.Row) -> ActionEnvelope:
        diff_data = json.loads(row["diff_preview"]) if row["diff_preview"] else None
        diff_preview = DiffPreview(**diff_data) if diff_data else None
        exec_res = json.loads(row["execution_result"]) if ("execution_result" in row.keys() and row["execution_result"]) else None

        return ActionEnvelope(
            action_id=row["action_id"],
            agent_id=row["agent_id"],
            session_id=row["session_id"],
            tool_name=row["tool_name"],
            tier=TierLevel(row["tier"]),
            risk_score=row["risk_score"],
            risk_factors=json.loads(row["risk_factors"]),
            payload=json.loads(row["payload"]),
            diff_preview=diff_preview,
            status=ApprovalStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
            user_signature=row["user_signature"],
            user_feedback=row["user_feedback"],
            execution_result=exec_res,
        )


class PolicyEnclave:
    """
    Central gatekeeper enforcing zero-trust boundaries on all agent actions.
    """

    # Static Tool Mapping
    TOOL_CLASSIFICATIONS: Dict[str, TierLevel] = {
        # TIER_0_OBSERVE: Read-only observations
        "gmail_read_threads": TierLevel.TIER_0_OBSERVE,
        "calendar_list_events": TierLevel.TIER_0_OBSERVE,
        "web_scrape_product_price": TierLevel.TIER_0_OBSERVE,
        "instagram_index_saves": TierLevel.TIER_0_OBSERVE,
        "arxiv_fetch_papers": TierLevel.TIER_0_OBSERVE,
        "github_scan_trending": TierLevel.TIER_0_OBSERVE,
        "ffmpeg_probe_metadata": TierLevel.TIER_0_OBSERVE,

        # TIER_1_STAGE: Staging and local generation
        "gmail_create_draft": TierLevel.TIER_1_STAGE,
        "whatsapp_stage_reply": TierLevel.TIER_1_STAGE,
        "cart_stage_items": TierLevel.TIER_1_STAGE,
        "ffmpeg_cut_video_highlight": TierLevel.TIER_1_STAGE,
        "calendar_propose_slot": TierLevel.TIER_1_STAGE,

        # TIER_2_COMMIT: High-stakes external mutations & state modifications
        "gmail_send_message": TierLevel.TIER_2_COMMIT,
        "whatsapp_send_message": TierLevel.TIER_2_COMMIT,
        "ecommerce_checkout_purchase": TierLevel.TIER_2_COMMIT,
        "calendar_create_event": TierLevel.TIER_2_COMMIT,
        "calendar_delete_event": TierLevel.TIER_2_COMMIT,
        "filesystem_delete_file": TierLevel.TIER_2_COMMIT,
        "cloud_provision_resources": TierLevel.TIER_2_COMMIT,
    }

    def __init__(self, db_path: Optional[str] = None):
        self.queue = ApprovalQueue(db_path=db_path)
        self.edit_delta_callback: Optional[Callable[[ActionEnvelope, Dict[str, Any]], Coroutine[Any, Any, None]]] = None

    def register_edit_delta_callback(
        self,
        callback: Callable[[ActionEnvelope, Dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        """
        Subscribes the EditDeltaEngine to capture user modifications to staged drafts.
        """
        self.edit_delta_callback = callback

    def evaluate_risk(self, tool_name: str, payload: Dict[str, Any]) -> Tuple[TierLevel, float, List[str]]:
        """
        Calculates dynamic risk score and enforces tier classification.
        """
        default_tier = self.TOOL_CLASSIFICATIONS.get(tool_name, TierLevel.TIER_2_COMMIT)
        risk_score = 0.0
        risk_factors: List[str] = []

        if default_tier == TierLevel.TIER_0_OBSERVE:
            risk_score = 0.05
            risk_factors.append("Read-only operation; zero external side effects")
            return default_tier, risk_score, risk_factors

        if default_tier == TierLevel.TIER_1_STAGE:
            risk_score = 0.25
            risk_factors.append("Staging operation; local state modification only")
            return default_tier, risk_score, risk_factors

        # Dynamic risk evaluation for TIER_2
        risk_score = 0.70
        risk_factors.append(f"Stateful mutation tool: {tool_name}")

        # Financial transaction detection
        cost = payload.get("amount") or payload.get("total_price") or payload.get("budget")
        if cost and float(cost) > 0.0:
            risk_score = min(1.0, risk_score + 0.20)
            risk_factors.append(f"Financial commitment detected: ${float(cost):.2f}")

        # External communication checks
        recipient = payload.get("recipient") or payload.get("to") or payload.get("chat_name")
        if recipient:
            risk_factors.append(f"Outbound communication targeted at: {recipient}")
            # If external or unverified contact
            if "@" in str(recipient) and not str(recipient).endswith("@trusteddomain.com"):
                risk_score = min(1.0, risk_score + 0.15)
                risk_factors.append("External domain recipient")

        # Destructive file or system ops
        if "delete" in tool_name.lower() or "drop" in tool_name.lower():
            risk_score = 0.95
            risk_factors.append("Irreversible deletion operation")

        return TierLevel.TIER_2_COMMIT, round(risk_score, 2), risk_factors

    def prepare_envelope(
        self,
        tool_name: str,
        payload: Dict[str, Any],
        agent_id: str,
        session_id: str,
        diff_preview: Optional[DiffPreview] = None,
    ) -> ActionEnvelope:
        tier, risk_score, risk_factors = self.evaluate_risk(tool_name, payload)

        return ActionEnvelope(
            action_id=str(uuid4()),
            agent_id=agent_id,
            session_id=session_id,
            tool_name=tool_name,
            tier=tier,
            risk_score=risk_score,
            risk_factors=risk_factors,
            payload=payload,
            diff_preview=diff_preview,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )

    async def execute_or_suspend(
        self,
        envelope: ActionEnvelope,
        execution_func: Callable[[], Coroutine[Any, Any, Any]],
        await_approval_timeout: Optional[float] = None,
    ) -> Any:
        """
        Intercepts tool execution:
        - TIER_0 and TIER_1 execute autonomously.
        - TIER_2 suspends into ApprovalQueue and pauses unless already signed.
        """
        # If pre-authorized by user signature
        if envelope.status == ApprovalStatus.APPROVED and envelope.user_signature:
            logger.info(f"Authorized TIER_2 action {envelope.action_id} executing with signature {envelope.user_signature[:8]}...")
            return await execution_func()

        if envelope.tier in (TierLevel.TIER_0_OBSERVE, TierLevel.TIER_1_STAGE):
            envelope.status = ApprovalStatus.APPROVED
            envelope.resolved_at = datetime.now(timezone.utc)
            return await execution_func()

        # TIER_2_COMMIT without valid signature -> Suspend
        future = self.queue.enqueue(envelope)

        if await_approval_timeout is not None and await_approval_timeout > 0:
            logger.info(f"Awaiting approval for {envelope.action_id} up to {await_approval_timeout}s...")
            try:
                resolved_envelope = await asyncio.wait_for(future, timeout=await_approval_timeout)
                if resolved_envelope.status in (ApprovalStatus.APPROVED, ApprovalStatus.EDITED):
                    # Check if edit delta learning is needed
                    if resolved_envelope.status == ApprovalStatus.EDITED and self.edit_delta_callback:
                        await self.edit_delta_callback(envelope, resolved_envelope.payload)
                    return await execution_func()
                else:
                    raise PermissionError(f"Action {envelope.action_id} was rejected by user.")
            except asyncio.TimeoutError:
                raise ActionSuspendedException(envelope)

        raise ActionSuspendedException(envelope)


# Singleton Enclave instance
_global_enclave = PolicyEnclave()


def get_policy_enclave() -> PolicyEnclave:
    return _global_enclave


def enforce_policy(
    tool_name: Optional[str] = None,
    tier: Optional[TierLevel] = None,
    await_approval_timeout: Optional[float] = None,
) -> Callable[[F], F]:
    """
    Decorator for swarm tool functions to enforce deterministic policy and HITL guardrails.
    """
    def decorator(func: F) -> F:
        effective_tool_name = tool_name or func.__name__

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            enclave = get_policy_enclave()
            payload = kwargs.copy()
            if args:
                payload["_positional_args"] = [str(a) for a in args]

            agent_id = kwargs.pop("_agent_id", "unspecified_agent")
            session_id = kwargs.pop("_session_id", str(uuid4()))
            user_signature = kwargs.pop("_user_signature", None)

            envelope = enclave.prepare_envelope(
                tool_name=effective_tool_name,
                payload=payload,
                agent_id=agent_id,
                session_id=session_id,
            )

            if user_signature:
                envelope.status = ApprovalStatus.APPROVED
                envelope.user_signature = user_signature

            async def run_target() -> Any:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                return func(*args, **kwargs)

            return await enclave.execute_or_suspend(
                envelope=envelope,
                execution_func=run_target,
                await_approval_timeout=await_approval_timeout,
            )

        return cast(F, wrapper)
    return decorator
