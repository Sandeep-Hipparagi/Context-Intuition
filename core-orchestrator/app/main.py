"""
VibeCore / Context-Intuition FastAPI Orchestrator.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Exposes authenticated WebSocket endpoints for the Chrome Extension bridge,
SSE/WebSocket streams for the Live Personal Dashboard, and REST endpoints
for the Zero-Trust Approval Queue and Context Intuition Ledger.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sys
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

from capabilities import mint_capability_token
from enclave import IdempotencyConflictError, get_policy_enclave

from app.agents.morning_brief import MorningBriefAgent
from app.config import get_or_create_bridge_token, settings
from app.dispatcher import BridgeDisconnectedError, CapabilityVerificationError, ToolDispatcher
from app.ledger.context_graph import ContextKnowledgeGraph
from app.ledger.edit_delta import EditDeltaEngine
from app.schemas import (
    ActionEnvelope,
    ApprovalStatus,
    BridgeMessage,
    BridgeMessageType,
    ContextNode,
    ContextNodeType,
    MorningBrief,
)
from app.tracing import generate_span_id, generate_trace_id, get_trace_store

logger = logging.getLogger("Orchestrator")
logging.basicConfig(level=logging.INFO)

# Shared Singletons
context_graph = ContextKnowledgeGraph()
edit_delta_engine = EditDeltaEngine()
policy_enclave = get_policy_enclave()
tool_dispatcher = ToolDispatcher()
scheduler = AsyncIOScheduler()

# Frame deduplication: bounded LRU cache for seen frame_ids (max 10000 entries)
class FrameDeduplicator:
    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self._seen: OrderedDict[str, None] = OrderedDict()

    def check_and_add(self, frame_id: str) -> bool:
        """
        Returns True if frame_id is new (not seen), False if duplicate.
        """
        if frame_id in self._seen:
            # Move to end (most recent)
            self._seen.move_to_end(frame_id)
            return False
        self._seen[frame_id] = None
        if len(self._seen) > self.max_size:
            self._seen.popitem(last=False)  # Remove oldest
        return True

    def clear(self):
        self._seen.clear()

frame_deduplicator = FrameDeduplicator()


async def run_scheduled_brief():
    logger.info("Executing scheduled overnight morning brief cycle...")
    agent = MorningBriefAgent(context_graph=context_graph, edit_delta_engine=edit_delta_engine)
    brief = await agent.execute_overnight_cycle(live_scouts=True)
    await manager.broadcast_to_dashboards("NEW_MORNING_BRIEF", brief.model_dump(mode="json"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start overnight background scheduler
    cron_expr = os.environ.get("VIBECORE_BRIEF_CRON", "30 5 * * *")
    try:
        scheduler.add_job(
            run_scheduled_brief,
            CronTrigger.from_crontab(cron_expr),
            id="overnight_morning_brief",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(f"Overnight Chief-of-Staff scheduler started with cron '{cron_expr}'.")
    except (ValueError, RuntimeError) as e:
        logger.warning(f"Could not start scheduler with cron '{cron_expr}': {e}")

    yield

    if scheduler.running:
        scheduler.shutdown()
        logger.info("Overnight scheduler stopped.")


app = FastAPI(
    title="VibeCore / Context-Intuition Agent Swarm",
    description="Local-first, privacy-preserving personal agent swarm runtime.",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def handle_edit_delta(envelope: ActionEnvelope, revised_payload: dict[str, Any]) -> None:
    await edit_delta_engine.learn_from_edit(envelope, revised_payload)


policy_enclave.register_edit_delta_callback(handle_edit_delta)


ALLOWED_ORIGIN_PATTERNS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

MAX_WS_FRAME_SIZE = 1_048_576  # 1 MB max frame size
MAX_BATCH_ITEMS = 200          # Max items per telemetry batch


def is_allowed_origin(origin: str | None) -> bool:
    """
    Validates WebSocket origin header against authorized clients and Chrome extension schemes.
    """
    if not origin or origin == "null":
        # Direct WebSocket connections from desktop tools or extensions
        return True
    if origin.startswith("chrome-extension://"):
        return True
    return origin in ALLOWED_ORIGIN_PATTERNS


def sanitize_dom_payload(raw_content: Any) -> str:
    """
    Sanitizes untrusted browser DOM inputs: strips null bytes, escapes delimiter
    breakout sequences, and isolates within security boundaries to mitigate
    indirect prompt injection attacks against downstream LLM parsers.
    """
    text = str(raw_content).replace("\x00", "").strip()
    # Escape closing and opening tags to prevent delimiter breakout or framing spoofing
    text = re.sub(r'<\s*/\s*untrusted_dom_payload\s*>', '&lt;/untrusted_dom_payload&gt;', text, flags=re.IGNORECASE)
    text = re.sub(r'<\s*untrusted_dom_payload\s*>', '&lt;untrusted_dom_payload&gt;', text, flags=re.IGNORECASE)
    return f"<untrusted_dom_payload>\n{text}\n</untrusted_dom_payload>"


def verify_ws_token(token: str | None) -> bool:
    """
    Validates the handshake token using constant-time comparison to mitigate timing attacks.
    Rejects empty or missing tokens strictly.
    """
    if not token or not token.strip():
        return False
    expected = get_or_create_bridge_token()
    if not expected or not expected.strip():
        return False
    return hmac.compare_digest(token.strip(), expected.strip())


# ============================================================================
# WebSocket Connection Manager for Chrome Extension & Dashboard Clients
# ============================================================================

class ConnectionManager:
    def __init__(self):
        self.browser_bridges: list[WebSocket] = []
        self.dashboard_clients: list[WebSocket] = []

    async def connect_browser(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.browser_bridges.append(websocket)
        logger.info(f"Browser bridge connected. Active count: {len(self.browser_bridges)}")

    def disconnect_browser(self, websocket: WebSocket) -> None:
        if websocket in self.browser_bridges:
            self.browser_bridges.remove(websocket)
            logger.info(f"Browser bridge disconnected. Active count: {len(self.browser_bridges)}")

    async def connect_dashboard(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.dashboard_clients.append(websocket)
        logger.info(f"Dashboard connected. Active count: {len(self.dashboard_clients)}")

    def disconnect_dashboard(self, websocket: WebSocket) -> None:
        if websocket in self.dashboard_clients:
            self.dashboard_clients.remove(websocket)
            logger.info(f"Dashboard disconnected. Active count: {len(self.dashboard_clients)}")

    async def broadcast_to_dashboards(self, event_type: str, data: Any) -> None:
        payload = json.dumps({"event": event_type, "data": data})
        for client in list(self.dashboard_clients):
            try:
                await client.send_text(payload)
            except (RuntimeError, WebSocketDisconnect, OSError) as e:
                logger.warning(f"Error sending to dashboard: {e}")

    async def send_to_browser(self, message: BridgeMessage) -> None:
        if not self.browser_bridges:
            logger.warning("No active browser bridges connected.")
            return
        payload = message.model_dump_json()
        for bridge in list(self.browser_bridges):
            try:
                await bridge.send_text(payload)
            except (RuntimeError, WebSocketDisconnect, OSError) as e:
                logger.warning(f"Error sending to browser bridge: {e}")


manager = ConnectionManager()
tool_dispatcher.set_browser_bridge(
    sender=manager.send_to_browser,
    is_connected=lambda: len(manager.browser_bridges) > 0,
)


# ============================================================================
# Authenticated WebSocket Endpoints
# ============================================================================

@app.websocket("/ws/browser-bridge")
async def browser_bridge_endpoint(
    websocket: WebSocket,
    token: Annotated[str | None, Query()] = None,
):
    # Verify origin header
    origin = websocket.headers.get("origin")
    if not is_allowed_origin(origin):
        logger.warning(f"Rejected browser bridge connection from untrusted origin: {origin}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Verify authentication token with constant-time check
    if not verify_ws_token(token):
        logger.warning("Rejected unauthorized browser bridge connection attempt.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect_browser(websocket)
    try:
        while True:
            raw_text = await websocket.receive_text()

            # Guard against oversized payloads
            if len(raw_text) > MAX_WS_FRAME_SIZE:
                logger.warning(f"Rejected oversized frame ({len(raw_text)} bytes > {MAX_WS_FRAME_SIZE})")
                await websocket.send_text(json.dumps({
                    "error": "FRAME_OVERSIZED",
                    "detail": f"Payload size exceeds limit of {MAX_WS_FRAME_SIZE} bytes",
                }))
                continue

            try:
                data = json.loads(raw_text)
                message = BridgeMessage(**data)
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as e:
                logger.warning(f"Malformed JSON/Message received: {e}")
                await websocket.send_text(json.dumps({
                    "error": "MALFORMED_PAYLOAD",
                    "detail": str(e),
                }))
                continue

            # Frame deduplication: skip already-seen frame_ids
            if not frame_deduplicator.check_and_add(message.message_id):
                logger.debug(f"Duplicate frame_id ignored: {message.message_id}")
                continue

            # Extract trace context from message metadata for distributed tracing
            trace_id = message.metadata.get("trace_id") if message.metadata else None
            span_id = message.metadata.get("span_id") if message.metadata else None
            if not trace_id:
                trace_id = generate_trace_id()
            if not span_id:
                span_id = generate_span_id()

            if message.msg_type == BridgeMessageType.HEARTBEAT:
                continue

            logger.info(f"[BRIDGE] Received {message.msg_type} from authenticated Chrome extension [trace_id={trace_id[:8]}]")

            if message.msg_type == BridgeMessageType.DOM_GROCERY_SNAPSHOT:
                title = message.payload.get("title", "Untitled Product")
                portal = message.payload.get("portal", "ecommerce")
                sanitized_title = sanitize_dom_payload(title)

                stored_payload = dict(message.payload)
                stored_payload["sanitized_title"] = sanitized_title

                context_graph.upsert_node(
                    ContextNode(
                        node_id=str(uuid4()),
                        node_type=ContextNodeType.FACT,
                        title=f"Price Snapshot: {title[:80]}",
                        content=stored_payload,
                        confidence=0.90,
                        source=f"grocery_tracker:{portal}",
                        tags=["grocery", "price_tracker", portal],
                    ),
                    agent_id="chrome_extension",
                    trace_id=trace_id,
                )
                await manager.broadcast_to_dashboards("GROCERY_PRICE_UPDATE", stored_payload)

            elif message.msg_type in (BridgeMessageType.WHATSAPP_INCOMING_MESSAGE, BridgeMessageType.DOM_MUTATION_OBSERVED):
                chat_name = message.payload.get("chat_name", "Unknown")
                raw_msg_text = message.payload.get("text", "")

                # Security hardening: isolate untrusted DOM payload
                sanitized_text = sanitize_dom_payload(raw_msg_text)
                stored_payload = dict(message.payload)
                stored_payload["sanitized_text"] = sanitized_text

                # Record observation node in context graph
                context_graph.upsert_node(
                    ContextNode(
                        node_id=str(uuid4()),
                        node_type=ContextNodeType.EPISODE,
                        title=f"WhatsApp Message from {chat_name}",
                        content=stored_payload,
                        confidence=0.95,
                        source="whatsapp_web_observer",
                        tags=["whatsapp", "communication", chat_name],
                    ),
                    agent_id="chrome_extension",
                    trace_id=trace_id,
                )
                await manager.broadcast_to_dashboards("NEW_WHATSAPP_MESSAGE", stored_payload)

            elif message.msg_type == BridgeMessageType.INSTAGRAM_SAVED_BATCH:
                items = message.payload.get("items", [])
                if len(items) > MAX_BATCH_ITEMS:
                    logger.warning(f"Trimming batch items from {len(items)} to {MAX_BATCH_ITEMS}")
                    items = items[:MAX_BATCH_ITEMS]

                for item in items:
                    caption = item.get("caption", "")
                    author = item.get("author", "creator")
                    sanitized_caption = sanitize_dom_payload(caption)

                    item_payload = dict(item)
                    item_payload["sanitized_caption"] = sanitized_caption

                    context_graph.upsert_node(
                        ContextNode(
                            node_id=str(uuid4()),
                            node_type=ContextNodeType.FACT,
                            title=f"Instagram Save from @{author}",
                            content=item_payload,
                            confidence=0.85,
                            source="instagram_archivist",
                            tags=["instagram", "saved_post", author],
                        ),
                        agent_id="chrome_extension",
                        trace_id=trace_id,
                    )
                await manager.broadcast_to_dashboards("INSTAGRAM_BATCH_INDEXED", {"count": len(items)})

            elif message.msg_type == BridgeMessageType.PAGE_STATE_CHANGED:
                # Generic E-commerce or DOM snapshot
                stored_payload = dict(message.payload)
                if "title" in stored_payload:
                    stored_payload["sanitized_title"] = sanitize_dom_payload(stored_payload["title"])
                await manager.broadcast_to_dashboards("PRICE_SNAPSHOT", stored_payload)

            elif message.msg_type == BridgeMessageType.SELECTOR_CANARY_WARNING:
                # Handle DOM canary warning from plugin registry
                payload = message.payload
                logger.warning(
                    f"[CANARY_WARNING] Plugin '{payload.get('pluginName')}' ({payload.get('pluginId')}) "
                    f"missing selectors on {payload.get('url')}: {payload.get('missingSelectors')}"
                )
                # Broadcast to dashboards for non-intrusive health alert
                await manager.broadcast_to_dashboards("SELECTOR_CANARY_WARNING", payload)

    except WebSocketDisconnect:
        manager.disconnect_browser(websocket)
    except Exception as e:
        logger.error(f"Browser bridge error: {e}")
        manager.disconnect_browser(websocket)


@app.websocket("/ws/dashboard")
async def dashboard_endpoint(
    websocket: WebSocket,
    token: Annotated[str | None, Query()] = None,
):
    # Verify origin header
    origin = websocket.headers.get("origin")
    if not is_allowed_origin(origin):
        logger.warning(f"Rejected dashboard connection from untrusted origin: {origin}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Verify authentication token
    if not verify_ws_token(token):
        logger.warning("Rejected unauthorized dashboard connection attempt.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect_dashboard(websocket)
    try:
        pending = policy_enclave.queue.list_pending()
        await websocket.send_text(json.dumps({
            "event": "INITIAL_PENDING_APPROVALS",
            "data": [p.model_dump(mode="json") for p in pending],
        }))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_dashboard(websocket)


# ============================================================================
# REST Endpoints: Auth Token Discovery & Zero-Trust Approvals
# ============================================================================

@app.get("/api/health")
async def health_check():
    """
    Lightweight health check endpoint returning system status and component telemetry.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "version": "0.3.0",
        "scheduler_running": scheduler.running,
        "browser_bridges_connected": len(manager.browser_bridges),
        "dashboard_clients_connected": len(manager.dashboard_clients),
        "profile": settings.PROFILE,
    }


@app.get("/api/auth/token")
async def get_bridge_token_info():
    """
    Returns the current active bridge token (only accessible locally).
    """
    return {
        "status": "active",
        "token": get_or_create_bridge_token(),
        "hint": "Provide this token in ?token=<token> when establishing WebSocket connections.",
    }


class ResolveActionRequest(BaseModel):
    status: ApprovalStatus
    user_signature: str | None = "sig_user_approved"
    edited_payload: dict[str, Any] | None = None
    user_feedback: str | None = None


@app.get("/api/approvals", response_model=list[ActionEnvelope])
async def list_pending_approvals():
    return policy_enclave.queue.list_pending()


@app.post("/api/approvals/{action_id}/resolve", response_model=ActionEnvelope)
async def resolve_approval(action_id: str, request: ResolveActionRequest):
    """
    Approves, Rejects, or Approves with Edits a staged Tier-2 action.
    Enforces atomic idempotency via database claim to prevent TOCTOU double-execution.
    Dispatches approved actions to ToolDispatcher with capability token.
    Triggers the Edit-Delta Learning Engine if the payload was edited.
    """
    if request.status in (ApprovalStatus.APPROVED, ApprovalStatus.EDITED):
        # 1. Atomically claim execution slot to prevent TOCTOU race conditions
        try:
            envelope = policy_enclave.queue.claim_for_execution(action_id)
        except IdempotencyConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        final_payload = (
            request.edited_payload
            if (request.status == ApprovalStatus.EDITED and request.edited_payload is not None)
            else envelope.payload
        )

        # Trigger Edit-Delta Learning if payload was revised
        if request.status == ApprovalStatus.EDITED and request.edited_payload:
            await edit_delta_engine.learn_from_edit(envelope, request.edited_payload)

        # Mint capability token for authorized execution
        capability_token = mint_capability_token(
            action_id=action_id,
            tool_name=envelope.tool_name,
            payload=final_payload,
            expires_in=60,  # 60 second TTL
            profile=settings.PROFILE,
        )

        # Dispatch the finalized payload to the registered execution handler with verified envelope_id and capability token
        try:
            execution_result = await tool_dispatcher.dispatch(
                envelope.tool_name,
                final_payload,
                envelope_id=action_id,
                capability_token=capability_token,
            )
        except BridgeDisconnectedError as e:
            # Recoverable browser disconnection: revert action back to PENDING state
            policy_enclave.queue.revert_to_pending(action_id)
            raise HTTPException(status_code=503, detail=str(e)) from e
        except CapabilityVerificationError as e:
            # Capability token verification failed - this should not happen for freshly minted tokens
            logger.error(f"Capability verification failed for {action_id}: {e}")
            policy_enclave.queue.revert_to_pending(action_id)
            raise HTTPException(status_code=500, detail=f"Capability verification failed: {e!s}") from e
        except Exception as e:
            logger.error(f"Execution error dispatching tool {envelope.tool_name}: {e}")
            policy_enclave.queue.revert_to_pending(action_id)
            raise HTTPException(status_code=500, detail=f"Tool execution failed: {e!s}") from e

        resolved = policy_enclave.queue.resolve(
            action_id=action_id,
            status=request.status,
            user_signature=request.user_signature,
            edited_payload=request.edited_payload,
            user_feedback=request.user_feedback,
            execution_result=execution_result,
        )
    else:
        # Rejection or non-executing resolution
        try:
            resolved = policy_enclave.queue.resolve(
                action_id=action_id,
                status=request.status,
                user_signature=request.user_signature,
                user_feedback=request.user_feedback,
            )
        except IdempotencyConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    await manager.broadcast_to_dashboards("ACTION_RESOLVED", resolved.model_dump(mode="json"))
    return resolved


@app.post("/api/brief/trigger", response_model=MorningBrief)
async def trigger_morning_brief():
    agent = MorningBriefAgent(context_graph=context_graph, edit_delta_engine=edit_delta_engine)
    brief = await agent.execute_overnight_cycle(live_scouts=True)
    await manager.broadcast_to_dashboards("NEW_MORNING_BRIEF", brief.model_dump(mode="json"))
    return brief


@app.get("/api/brief/latest", response_model=MorningBrief)
async def get_latest_morning_brief():
    """
    Returns the most recent MorningBrief synthesized by the Overnight Chief-of-Staff.
    If no brief exists, compiles a new brief on-demand.
    """
    nodes = context_graph.find_nodes(node_type=ContextNodeType.EPISODE, tag="latest_brief")
    if nodes:
        content = nodes[0].content
        return MorningBrief(**content)

    agent = MorningBriefAgent(context_graph=context_graph, edit_delta_engine=edit_delta_engine)
    brief = await agent.execute_overnight_cycle(live_scouts=False)
    return brief


@app.get("/api/context/rules")
async def list_learned_rules():
    return edit_delta_engine.get_applicable_rules()


@app.get("/api/context/nodes")
async def query_context_nodes(
    node_type: Annotated[ContextNodeType | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
):
    nodes = context_graph.find_nodes(node_type=node_type, tag=tag)
    return [n.model_dump(mode="json") for n in nodes]


# ============================================================================
# Event-Sourced Context Graph Inspection Endpoints
# ============================================================================

@app.get("/api/context/nodes/{node_id}/history")
async def get_node_history(node_id: str):
    """
    Returns the chronological causal chain of mutations for a node.
    Immutable event log - never modified after creation.
    """
    events = context_graph.get_node_history(node_id)
    return [e.model_dump(mode="json") for e in events]


@app.get("/api/context/replay")
async def replay_context_graph(as_of: str = Query(..., description="ISO8601 timestamp to replay state to")):
    """
    Reconstructs the exact materialized graph state as it existed at a historical timestamp.
    Replays all events chronologically up to the given timestamp without mutating live tables.
    """
    try:
        as_of_dt = datetime.fromisoformat(as_of)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail="Invalid timestamp format. Use ISO8601 (e.g., 2024-01-15T10:30:00Z)",
        ) from e

    nodes = context_graph.replay_until(as_of_dt)
    return [n.model_dump(mode="json") for n in nodes]


@app.get("/api/context/search")
async def search_context(
    q: Annotated[str, Query(min_length=1, description="Search query")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    semantic_weight: Annotated[float, Query(ge=0.0, le=1.0)] = 0.6,
    node_type: Annotated[ContextNodeType | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
):
    """
    Hybrid semantic + temporal search over the context graph.

    Combines cosine similarity of query embedding with node embeddings
    and normalized temporal decayed relevance.

    hybrid_score = semantic_weight * semantic_similarity + (1 - semantic_weight) * normalized_decayed_relevance
    """
    results = context_graph.search_hybrid(
        query=q,
        limit=limit,
        semantic_weight=semantic_weight,
        node_type=node_type,
        tag=tag,
    )
    return [
        {"node": node.model_dump(mode="json"), "score": score}
        for node, score in results
    ]


# ============================================================================
# Distributed Tracing Endpoints
# ============================================================================

@app.get("/api/traces/recent")
async def get_recent_traces(limit: int = Query(100, ge=1, le=500)):
    """Get recent trace summaries (one per trace_id)."""
    trace_store = get_trace_store()
    traces = trace_store.get_recent_traces(limit=limit)
    return traces


@app.get("/api/traces/{trace_id}")
async def get_trace(trace_id: str):
    """Get all spans for a trace_id, ordered by start time (W3C trace waterfall)."""
    trace_store = get_trace_store()
    spans = trace_store.get_trace(trace_id)
    if not spans:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return [span.__dict__ for span in spans]
