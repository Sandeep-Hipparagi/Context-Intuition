"""
VibeCore / Context-Intuition Core Data Schemas.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Strictly typed Pydantic models for the longitudinal context engine,
behavioral edit-delta playbook, zero-trust policy enclave, and agent RPC envelopes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

# ============================================================================
# 1. Zero-Trust Policy & Guardrail Enums
# ============================================================================

class TierLevel(str, Enum):
    """
    Deterministic security classification for tool executions.
    """
    TIER_0_OBSERVE = "TIER_0_OBSERVE"   # Autonomous read-only (calendar, inbox scan, price scraping)
    TIER_1_STAGE = "TIER_1_STAGE"       # Autonomous staging (drafting emails/DMs, cart building, video cuts)
    TIER_2_COMMIT = "TIER_2_COMMIT"     # Hard pause & explicit user signoff required (send, delete, buy, write)


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EDITED = "EDITED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


# ============================================================================
# 2. Longitudinal Context Intuition & Knowledge Graph Schemas
# ============================================================================

class ContextNodeType(str, Enum):
    FACT = "FACT"
    PREFERENCE = "PREFERENCE"
    EPISODE = "EPISODE"
    GOAL = "GOAL"
    ENTITY = "ENTITY"
    TRAJECTORY = "TRAJECTORY"
    DECISION_HISTORY = "DECISION_HISTORY"


class ContextEdge(BaseModel):
    source_id: str
    target_id: str
    relation: str = Field(..., description="Semantic relation, e.g., 'CONTRADICTS', 'PREFERS', 'PART_OF'")
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContextEventType(str, Enum):
    NODE_CREATED = "NODE_CREATED"
    NODE_UPDATED = "NODE_UPDATED"
    CONFIDENCE_REINFORCED = "CONFIDENCE_REINFORCED"
    DECAY_APPLIED = "DECAY_APPLIED"
    CONSOLIDATED_INTO_TRAJECTORY = "CONSOLIDATED_INTO_TRAJECTORY"
    NODE_PRUNED = "NODE_PRUNED"


class ContextEvent(BaseModel):
    """
    Immutable event record for event-sourced ContextKnowledgeGraph.
    Every mutation to the graph is recorded as an event for audit and time-travel.
    """
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    node_id: str
    event_type: ContextEventType
    delta_payload: dict[str, Any] = Field(default_factory=dict, description="The change that was applied")
    agent_id: str | None = Field(default=None, description="Agent that triggered this event")
    trace_id: str | None = Field(default=None, description="W3C traceparent trace_id for distributed tracing")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContextNode(BaseModel):
    """
    Atom of the longitudinal state ledger. Stores facts, evolving preferences,
    and trajectories with temporal decay characteristics.
    """
    node_id: str = Field(default_factory=lambda: str(uuid4()))
    node_type: ContextNodeType
    title: str = Field(..., max_length=256)
    content: dict[str, Any] = Field(default_factory=dict, description="Structured payload or text entity attributes")
    embedding: list[float] | None = Field(default=None, description="Dense vector embedding for semantic search")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Epistemic certainty")
    decay_rate: float = Field(default=0.05, ge=0.0, le=1.0, description="Decay factor per day for nightly consolidation")
    access_count: int = Field(default=0)
    last_accessed: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = Field(..., description="Originating agent or telemetry pipeline (e.g. 'edit_delta_whatsapp')")
    tags: list[str] = Field(default_factory=list)
    edges: list[ContextEdge] = Field(default_factory=list)

    def compute_decayed_relevance(self, as_of: datetime | None = None) -> float:
        """
        Computes current relevance score given elapsed time and reinforcement frequency.
        """
        ref_time = as_of or datetime.now(UTC)
        elapsed_days = max(0.0, (ref_time - self.last_accessed).total_seconds() / 86400.0)
        # Standard half-life decay formula reinforced by access frequency
        reinforcement_boost = min(2.0, 1.0 + (0.1 * self.access_count))
        base_retention = (1.0 - self.decay_rate) ** elapsed_days
        return round(float(self.confidence * base_retention * reinforcement_boost), 4)


class BehavioralCategory(str, Enum):
    COMMUNICATION_TONE = "COMMUNICATION_TONE"
    SCHEDULING = "SCHEDULING"
    FINANCIAL_THRESHOLD = "FINANCIAL_THRESHOLD"
    ATTENTION_FILTER = "ATTENTION_FILTER"
    DELEGATION_BOUNDARY = "DELEGATION_BOUNDARY"


class BehavioralRule(BaseModel):
    """
    An inductive rule generated via Edit-Delta learning when the user revises an agent's draft.
    Prevents context-window bloat by distilling preferences into explicit policy directives.
    """
    rule_id: str = Field(default_factory=lambda: str(uuid4()))
    category: BehavioralCategory
    trigger_pattern: str = Field(..., description="Condition under which this rule fires (e.g. 'recipient == investor')")
    action_directive: str = Field(..., description="Implicit preference instruction (e.g. 'Use concise bullet points, no pleasantries')")
    original_draft_sample: str | None = None
    user_revised_sample: str | None = None
    inferred_delta_rationale: str = Field(..., description="Why the semantic shift occurred based on the edit diff")
    weight: float = Field(default=1.0, ge=0.1, le=5.0, description="Confidence/strength of this learned heuristic")
    reinforcement_count: int = Field(default=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_triggered_at: datetime | None = None


# ============================================================================
# 3. Action Envelope & Policy Guardrail Payloads
# ============================================================================

class DiffPreview(BaseModel):
    """
    Visual and semantic comparison presented in the dashboard or mobile push
    before approving a TIER_2_COMMIT action.
    """
    original: str | None = None
    proposed: str | None = None
    structured_diff: dict[str, Any] | None = None
    affected_resource: str | None = None


class ActionEnvelope(BaseModel):
    """
    Wrapper for every tool execution across the entire swarm.
    Enforces deterministic inspection by the Zero-Trust Policy Enclave.
    """
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str = Field(..., description="Identifier of calling agent (e.g., 'browser_whatsapp_agent')")
    session_id: str = Field(..., description="Current user session or trace ID")
    tool_name: str = Field(..., description="Canonical tool name (e.g. 'gmail_send', 'checkout_cart')")
    tier: TierLevel
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_factors: list[str] = Field(default_factory=list, description="Audit trail justifying tier & risk calculation")
    payload: dict[str, Any] = Field(..., description="Tool arguments and parameters")
    diff_preview: DiffPreview | None = None
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    user_signature: str | None = Field(default=None, description="Cryptographic signature or token approving action")
    user_feedback: str | None = Field(default=None, description="Optional user corrections or instructions")
    execution_result: dict[str, Any] | None = Field(default=None, description="Telemetry/output from post-approval execution")
    trace_id: str | None = Field(default=None, description="W3C traceparent trace_id for end-to-end distributed tracing")
    span_id: str | None = Field(default=None, description="W3C traceparent span_id for this operation")


# ============================================================================
# 4. Chrome Extension <-> Local Bridge WebSocket Messages
# ============================================================================

class BridgeMessageType(str, Enum):
    HANDSHAKE = "HANDSHAKE"
    HEARTBEAT = "HEARTBEAT"
    PAGE_STATE_CHANGED = "PAGE_STATE_CHANGED"
    DOM_MUTATION_OBSERVED = "DOM_MUTATION_OBSERVED"
    EXECUTE_COMMAND = "EXECUTE_COMMAND"
    COMMAND_RESULT = "COMMAND_RESULT"
    COMMIT_ACTION = "COMMIT_ACTION"
    DOM_GROCERY_SNAPSHOT = "DOM_GROCERY_SNAPSHOT"
    WHATSAPP_INCOMING_MESSAGE = "WHATSAPP_INCOMING_MESSAGE"
    INSTAGRAM_SAVED_BATCH = "INSTAGRAM_SAVED_BATCH"
    SELECTOR_CANARY_WARNING = "SELECTOR_CANARY_WARNING"
    ERROR = "ERROR"


class BridgeMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    msg_type: BridgeMessageType
    tab_id: int | None = None
    url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] | None = Field(default_factory=dict, description="Optional metadata including trace_id, span_id for distributed tracing")


# ============================================================================
# 5. Overnight Morning Brief Schemas
# ============================================================================

class TriageUrgency(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class CommitmentItem(BaseModel):
    item_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str = Field(..., description="'gmail' | 'calendar' | 'whatsapp'")
    summary: str
    counterparty: str
    due_datetime: datetime | None = None
    confidence: float
    staged_reply_action_id: str | None = None


class ScheduleConflict(BaseModel):
    event_a_title: str
    event_b_title: str
    start_time: datetime
    end_time: datetime
    recommended_resolution: str


class TechRadarItem(BaseModel):
    title: str
    url: str
    source: str = Field(..., description="'arxiv' | 'github'")
    relevance_rationale: str = Field(..., description="Exact alignment with user's current project or codebase")
    trajectory_tag: str


class MorningBrief(BaseModel):
    brief_id: str = Field(default_factory=lambda: str(uuid4()))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    window_hours: int = 72
    executive_summary: str
    commitments_detected: list[CommitmentItem] = Field(default_factory=list)
    schedule_conflicts: list[ScheduleConflict] = Field(default_factory=list)
    pending_approvals: list[ActionEnvelope] = Field(default_factory=list)
    tech_radar: list[TechRadarItem] = Field(default_factory=list)
    subscription_alerts: list[str] = Field(default_factory=list)
