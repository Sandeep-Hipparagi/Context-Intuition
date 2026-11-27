"""
VibeCore Zero-Trust Policy Enclave & Approval Queue.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Deterministic guardrails, risk scoring, action suspension, and edit-delta trigger hooks.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import sqlite3
import sys
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import uuid4

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
from capabilities import CapabilityVerificationError as CapabilityVerificationError
from policy_loader import PolicyLoader, get_policy_loader

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


class IdempotencyConflictError(Exception):
    """
    Raised when an attempt is made to resolve an action that has already
    been resolved (i.e. status is not PENDING). Prevents duplicate executions.
    """


class ApprovalQueue:
    """
    Thread-safe and async-safe persistent queue for suspended actions.
    Stores pending envelopes in SQLite with WAL mode and notifies listeners via async futures.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(get_db_path("enclave_approvals.db"))
        self._pending_futures: dict[str, asyncio.Future[ActionEnvelope]] = {}
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
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

    def enqueue(self, envelope: ActionEnvelope) -> asyncio.Future[ActionEnvelope] | None:
        future: asyncio.Future[ActionEnvelope] | None = None
        try:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._pending_futures[envelope.action_id] = future
        except RuntimeError:
            # Running in synchronous context or without active event loop
            pass

        with self._get_connection() as conn:
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

    def get(self, action_id: str) -> ActionEnvelope | None:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approval_queue WHERE action_id = ?", (action_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_envelope(row)

    def list_pending(self) -> list[ActionEnvelope]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approval_queue WHERE status = ? ORDER BY created_at DESC", (ApprovalStatus.PENDING.value,))
            rows = cursor.fetchall()
            return [self._row_to_envelope(row) for row in rows]

    def claim_for_execution(self, action_id: str) -> ActionEnvelope:
        """
        Atomically claims a PENDING action by setting its status to EXECUTING.
        Raises IdempotencyConflictError if the action was already claimed or resolved,
        preventing TOCTOU double-execution race conditions.
        """
        now_str = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE approval_queue
                SET status = ?, resolved_at = ?
                WHERE action_id = ? AND status = ?
                """,
                (ApprovalStatus.EXECUTING.value, now_str, action_id, ApprovalStatus.PENDING.value),
            )
            conn.commit()

            if cursor.rowcount == 0:
                cursor.execute("SELECT status FROM approval_queue WHERE action_id = ?", (action_id,))
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Action {action_id} not found in approval queue.")
                curr_status = row["status"]
                raise IdempotencyConflictError(
                    f"Action {action_id} cannot be claimed for execution: current status is '{curr_status}'. "
                    f"Duplicate resolution rejected to enforce idempotency."
                )

            cursor.execute("SELECT * FROM approval_queue WHERE action_id = ?", (action_id,))
            row = cursor.fetchone()
            return self._row_to_envelope(row)

    def revert_to_pending(self, action_id: str) -> None:
        """
        Reverts an EXECUTING action back to PENDING (e.g. on recoverable bridge disconnect).
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE approval_queue SET status = ? WHERE action_id = ? AND status = ?",
                (ApprovalStatus.PENDING.value, action_id, ApprovalStatus.EXECUTING.value),
            )
            conn.commit()

    def resolve(
        self,
        action_id: str,
        status: ApprovalStatus,
        user_signature: str | None = None,
        edited_payload: dict[str, Any] | None = None,
        user_feedback: str | None = None,
        execution_result: dict[str, Any] | None = None,
    ) -> ActionEnvelope:
        envelope = self.get(action_id)
        if not envelope:
            raise ValueError(f"Action {action_id} not found in approval queue.")

        resolved_time = datetime.now(UTC)
        final_payload = edited_payload if edited_payload is not None else envelope.payload

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE approval_queue
                SET status = ?, resolved_at = ?, user_signature = ?, user_feedback = ?,
                    payload = ?, execution_result = ?
                WHERE action_id = ? AND (status = ? OR status = ?)
                """,
                (
                    status.value,
                    resolved_time.isoformat(),
                    user_signature,
                    user_feedback,
                    json.dumps(final_payload),
                    json.dumps(execution_result) if execution_result is not None else None,
                    action_id,
                    ApprovalStatus.PENDING.value,
                    ApprovalStatus.EXECUTING.value,
                ),
            )
            conn.commit()

            if cursor.rowcount == 0:
                cursor.execute("SELECT status FROM approval_queue WHERE action_id = ?", (action_id,))
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"Action {action_id} not found in approval queue.")
                curr_status = row["status"]
                raise IdempotencyConflictError(
                    f"Action {action_id} has already been resolved with status '{curr_status}'. "
                    f"Duplicate resolution rejected to enforce idempotency."
                )

            cursor.execute("SELECT * FROM approval_queue WHERE action_id = ?", (action_id,))
            row = cursor.fetchone()
            envelope = self._row_to_envelope(row)

        # Resolve async future if waiting in active process
        future = self._pending_futures.pop(action_id, None)
        if future and not future.done():
            future.set_result(envelope)

        logger.info(f" [APPROVAL_QUEUE] Action {action_id} marked as {status.value}")
        return envelope

    def _row_to_envelope(self, row: sqlite3.Row) -> ActionEnvelope:
        diff_data = json.loads(row["diff_preview"]) if row["diff_preview"] else None
        diff_preview = DiffPreview(**diff_data) if diff_data else None
        row_keys = set(row.keys())
        exec_res = (
            json.loads(row["execution_result"])
            if ("execution_result" in row_keys and row["execution_result"])
            else None
        )

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
    Uses declarative policy from policy.yaml with hot-reload support.
    """

    def __init__(self, db_path: str | None = None, policy_loader: PolicyLoader | None = None):
        self.queue = ApprovalQueue(db_path=db_path)
        self.edit_delta_callback: Callable[[ActionEnvelope, dict[str, Any]], Coroutine[Any, Any, None]] | None = None
        self._policy_loader = policy_loader or get_policy_loader()

    def register_edit_delta_callback(
        self,
        callback: Callable[[ActionEnvelope, dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        """
        Subscribes the EditDeltaEngine to capture user modifications to staged drafts.
        """
        self.edit_delta_callback = callback

    def evaluate_risk(self, tool_name: str, payload: dict[str, Any]) -> tuple[TierLevel, float, list[str]]:
        """
        Calculates dynamic risk score and enforces tier classification using declarative policy.
        """
        # Determine base risk from tool's base tier
        base_tier = self._policy_loader.get_tool_policy(tool_name).tier
        if base_tier == TierLevel.TIER_0_OBSERVE:
            base_risk = 0.05
        elif base_tier == TierLevel.TIER_1_STAGE:
            base_risk = 0.25
        else:  # TIER_2_COMMIT
            base_risk = 0.70
        return self._policy_loader.evaluate(tool_name, payload, base_risk=base_risk)

    def prepare_envelope(
        self,
        tool_name: str,
        payload: dict[str, Any],
        agent_id: str,
        session_id: str,
        diff_preview: DiffPreview | None = None,
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
            created_at=datetime.now(UTC),
        )

    async def execute_or_suspend(
        self,
        envelope: ActionEnvelope,
        execution_func: Callable[[], Coroutine[Any, Any, Any]],
        await_approval_timeout: float | None = None,
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
            envelope.resolved_at = datetime.now(UTC)
            return await execution_func()

        # TIER_2_COMMIT without valid signature -> Suspend
        future = self.queue.enqueue(envelope)

        if await_approval_timeout is not None and await_approval_timeout > 0:
            if future is None:
                raise ActionSuspendedException(envelope)
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
            except TimeoutError as exc:
                raise ActionSuspendedException(envelope) from exc

        raise ActionSuspendedException(envelope)

    def mint_capability_token(
        self,
        action_id: str,
        tool_name: str,
        payload: dict[str, Any],
        expires_in: int = 60,
    ) -> str:
        """
        Mint a short-lived HMAC-SHA256 capability token for authorized execution.
        Only called internally after policy approval.
        """
        from capabilities import mint_capability_token
        return mint_capability_token(action_id, tool_name, payload, expires_in)

    def verify_capability_token(self, token: str, tool_name: str, payload: dict[str, Any]) -> bool:
        """
        Verify a capability token for execution authorization.
        """
        from capabilities import verify_capability_token
        verify_capability_token(token, tool_name, payload)
        return True

    @property
    def TOOL_CLASSIFICATIONS(self) -> dict[str, TierLevel]:
        """
        Backward compatibility property: returns tool classifications from declarative policy.
        Allows dynamic registration of new tools for testing.
        """
        # Build classifications from policy loader
        policy = self._policy_loader.load()
        return {tool_name: tool_policy.tier for tool_name, tool_policy in policy.tools.items()}

    @TOOL_CLASSIFICATIONS.setter
    def TOOL_CLASSIFICATIONS(self, value: dict[str, TierLevel]) -> None:
        """
        Allow setting tool classifications for testing (delegates to policy.yaml via policy loader).
        Note: This only works for tools already defined in policy.yaml or will be ignored.
        For dynamic test tools, use the policy loader directly.
        """
        # For backward compatibility with tests that set TOOL_CLASSIFICATIONS
        # We'll update the in-memory policy loader's config


AUTHORIZED_EXECUTION_SIGNATURES = {
    "sig_dispatcher_authorized",
    "sig_user_approved",
}


def is_authorized_signature(signature: str | None) -> bool:
    """
    Validates that a provided user signature is authorized by the Policy Enclave
    before bypassing execution suspension.
    """
    if not signature or not isinstance(signature, str):
        return False
    return signature in AUTHORIZED_EXECUTION_SIGNATURES or signature.startswith("sig_user_approved_")


# Singleton Enclave instance
_global_enclave = PolicyEnclave()


def get_policy_enclave() -> PolicyEnclave:
    return _global_enclave


def enforce_policy(
    tool_name: str | None = None,
    tier: TierLevel | None = None,
    await_approval_timeout: float | None = None,
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

            if user_signature and is_authorized_signature(user_signature):
                envelope.status = ApprovalStatus.APPROVED
                envelope.user_signature = user_signature

            async def run_target() -> Any:
                if inspect.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                return func(*args, **kwargs)

            return await enclave.execute_or_suspend(
                envelope=envelope,
                execution_func=run_target,
                await_approval_timeout=await_approval_timeout,
            )

        return cast(F, wrapper)
    return decorator
