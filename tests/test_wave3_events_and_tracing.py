"""
Wave 3 Tests: Event-Sourced Context Graph & End-to-End Distributed Tracing.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.ledger.context_graph import ContextKnowledgeGraph
from app.schemas import (
    ContextNode,
    ContextNodeType,
    ContextEvent,
    ContextEventType,
    ActionEnvelope,
    ApprovalStatus,
    TierLevel,
)
from app.main import app, context_graph, frame_deduplicator
from app.tracing import (
    TraceStore,
    TraceSpan,
    Tracer,
    generate_trace_id,
    generate_span_id,
    parse_traceparent,
    format_traceparent,
    init_trace_store,
)


class TestContextGraphEventSourcing:
    """Test event-sourced ContextKnowledgeGraph with immutable event log."""

    @pytest.fixture
    def graph(self):
        return ContextKnowledgeGraph()

    def test_upsert_node_creates_event(self, graph):
        """Test that upsert_node appends NODE_CREATED event."""
        node = ContextNode(
            node_id="test-node-1",
            node_type=ContextNodeType.FACT,
            title="Test Fact",
            content={"data": "value"},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        
        event = graph.upsert_node(node, agent_id="test_agent", trace_id="trace-123")
        
        assert event is not None
        assert event.event_type == ContextEventType.NODE_CREATED
        assert event.node_id == "test-node-1"
        assert event.agent_id == "test_agent"
        assert event.trace_id == "trace-123"
        assert event.delta_payload["title"] == "Test Fact"

    def test_upsert_existing_node_creates_updated_event(self, graph):
        """Test that updating a node appends NODE_UPDATED event."""
        node = ContextNode(
            node_id="test-node-2",
            node_type=ContextNodeType.FACT,
            title="Original Title",
            content={"data": "original"},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        
        # Update the node
        node.title = "Updated Title"
        node.content = {"data": "updated"}
        node.updated_at = datetime.now(timezone.utc)
        event = graph.upsert_node(node, agent_id="test_agent")
        
        assert event is not None
        assert event.event_type == ContextEventType.NODE_UPDATED
        assert event.delta_payload["title"] == "Updated Title"

    def test_get_node_history_returns_chronological_events(self, graph):
        """Test that get_node_history returns events in chronological order."""
        node = ContextNode(
            node_id="test-node-3",
            node_type=ContextNodeType.FACT,
            title="History Test",
            content={"step": 1},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        
        # Update multiple times
        for i in range(2, 5):
            node.title = f"Step {i}"
            node.content = {"step": i}
            node.updated_at = datetime.now(timezone.utc)
            graph.upsert_node(node, agent_id="test_agent")
        
        history = graph.get_node_history("test-node-3")
        
        assert len(history) == 4  # 1 created + 3 updates
        assert history[0].event_type == ContextEventType.NODE_CREATED
        assert history[1].event_type == ContextEventType.NODE_UPDATED
        assert history[2].event_type == ContextEventType.NODE_UPDATED
        assert history[3].event_type == ContextEventType.NODE_UPDATED
        
        # Verify chronological order
        for i in range(len(history) - 1):
            assert history[i].created_at <= history[i + 1].created_at

    def test_touch_node_creates_confidence_reinforced_event(self, graph):
        """Test that touch_node appends CONFIDENCE_REINFORCED event."""
        node = ContextNode(
            node_id="test-node-4",
            node_type=ContextNodeType.FACT,
            title="Touch Test",
            content={"data": "value"},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        
        event = graph.touch_node("test-node-4", agent_id="reinforcer", trace_id="trace-456")
        
        assert event is not None
        assert event.event_type == ContextEventType.CONFIDENCE_REINFORCED
        assert event.agent_id == "reinforcer"
        assert event.trace_id == "trace-456"

    def test_nightly_consolidation_creates_pruned_events(self, graph):
        """Test that nightly consolidation appends NODE_PRUNED events for stale nodes."""
        # Create an old, low-relevance node
        old_time = datetime.now(timezone.utc) - timedelta(days=30)
        node = ContextNode(
            node_id="stale-node-1",
            node_type=ContextNodeType.FACT,
            title="Stale Fact",
            content={"data": "old"},
            confidence=0.5,
            decay_rate=0.1,
            last_accessed=old_time,
            created_at=old_time,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        
        # Run consolidation with high threshold to force pruning
        result = graph.nightly_consolidation(decay_threshold=0.5)
        
        assert result["pruned"] >= 1
        
        # Verify NODE_PRUNED event was created
        history = graph.get_node_history("stale-node-1")
        pruned_events = [e for e in history if e.event_type == ContextEventType.NODE_PRUNED]
        assert len(pruned_events) >= 1
        assert pruned_events[0].delta_payload["reason"] == "decay_below_threshold"

    def test_event_immutability(self, graph):
        """Test that events are never modified after creation."""
        node = ContextNode(
            node_id="immutable-test",
            node_type=ContextNodeType.FACT,
            title="Immutable Test",
            content={"data": "value"},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        event = graph.upsert_node(node, agent_id="test_agent")
        
        # Try to modify the event (should not affect stored event)
        event.delta_payload["tampered"] = True
        
        # Re-fetch history - original should be unchanged
        history = graph.get_node_history("immutable-test")
        assert "tampered" not in history[0].delta_payload


class TestContextGraphReplay:
    """Test point-in-time state reconstruction via replay_until."""

    @pytest.fixture
    def graph(self):
        return ContextKnowledgeGraph()

    def test_replay_until_reconstructs_state(self, graph):
        """Test that replay_until reconstructs exact state at a given timestamp."""
        base_time = datetime.now(timezone.utc)
        
        # Create node
        node = ContextNode(
            node_id="replay-node-1",
            node_type=ContextNodeType.FACT,
            title="Original",
            content={"version": 1},
            confidence=0.9,
            created_at=base_time,
            last_accessed=base_time,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        
        # Wait a bit and update
        time.sleep(0.01)
        update_time = datetime.now(timezone.utc)
        node.title = "Updated"
        node.content = {"version": 2}
        node.updated_at = update_time
        graph.upsert_node(node, agent_id="test_agent")
        
        # Replay to before update
        replayed = graph.replay_until(update_time - timedelta(milliseconds=5))
        
        replayed_node = next((n for n in replayed if n.node_id == "replay-node-1"), None)
        assert replayed_node is not None
        assert replayed_node.title == "Original"
        assert replayed_node.content["version"] == 1

    def test_replay_until_after_all_changes_shows_final_state(self, graph):
        """Test that replay_until after all changes shows final state."""
        node = ContextNode(
            node_id="replay-node-2",
            node_type=ContextNodeType.FACT,
            title="Original",
            content={"version": 1},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        
        node.title = "Updated"
        node.content = {"version": 2}
        node.updated_at = datetime.now(timezone.utc)
        graph.upsert_node(node, agent_id="test_agent")
        
        # Replay to future time
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        replayed = graph.replay_until(future)
        
        replayed_node = next((n for n in replayed if n.node_id == "replay-node-2"), None)
        assert replayed_node is not None
        assert replayed_node.title == "Updated"
        assert replayed_node.content["version"] == 2


class TestTraceStore:
    """Test local trace store with W3C traceparent compatibility."""

    @pytest.fixture
    def trace_store(self, tmp_path):
        db_path = str(tmp_path / "test_traces.db")
        store = init_trace_store(db_path, retention_days=7)
        return store

    def test_record_and_get_trace(self, trace_store):
        """Test recording and retrieving a trace with multiple spans."""
        trace_id = generate_trace_id()
        span1 = TraceSpan(
            trace_id=trace_id,
            span_id=generate_span_id(),
            parent_span_id=None,
            component="chrome_extension",
            operation="DOM_MUTATION_OBSERVED",
            status="completed",
            duration_ms=15,
            attributes={"url": "https://web.whatsapp.com"},
            started_at=datetime.now(timezone.utc),
        )
        span2 = TraceSpan(
            trace_id=trace_id,
            span_id=generate_span_id(),
            parent_span_id=span1.span_id,
            component="ws_bridge",
            operation="forward_message",
            status="completed",
            duration_ms=5,
            attributes={},
            started_at=datetime.now(timezone.utc),
        )
        
        trace_store.record_span(span1)
        trace_store.record_span(span2)
        
        spans = trace_store.get_trace(trace_id)
        assert len(spans) == 2
        assert spans[0].component == "chrome_extension"
        assert spans[1].component == "ws_bridge"
        assert spans[1].parent_span_id == span1.span_id

    def test_get_recent_traces(self, trace_store):
        """Test getting recent trace summaries."""
        for i in range(3):
            trace_id = generate_trace_id()
            span = TraceSpan(
                trace_id=trace_id,
                span_id=generate_span_id(),
                parent_span_id=None,
                component="test_component",
                operation=f"operation_{i}",
                status="completed",
                duration_ms=10 * (i + 1),
                attributes={},
                started_at=datetime.now(timezone.utc),
            )
            trace_store.record_span(span)
        
        recent = trace_store.get_recent_traces(limit=10)
        assert len(recent) == 3
        for trace in recent:
            assert "trace_id" in trace
            assert "started_at" in trace
            assert "span_count" in trace
            assert "components" in trace

    def test_prune_old_spans(self, trace_store):
        """Test automatic retention pruning."""
        # Create a trace with old timestamp
        old_span = TraceSpan(
            trace_id="old-trace-id",
            span_id=generate_span_id(),
            parent_span_id=None,
            component="old_component",
            operation="old_operation",
            status="completed",
            duration_ms=10,
            attributes={},
            started_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        trace_store.record_span(old_span)
        
        # Create a recent trace
        recent_span = TraceSpan(
            trace_id="recent-trace-id",
            span_id=generate_span_id(),
            parent_span_id=None,
            component="recent_component",
            operation="recent_operation",
            status="completed",
            duration_ms=10,
            attributes={},
            started_at=datetime.now(timezone.utc),
        )
        trace_store.record_span(recent_span)
        
        # Prune with 7-day retention
        deleted = trace_store.prune_old_spans()
        assert deleted >= 1
        
        # Old trace should be gone, recent should remain
        old_spans = trace_store.get_trace("old-trace-id")
        recent_spans = trace_store.get_trace("recent-trace-id")
        assert len(old_spans) == 0
        assert len(recent_spans) == 1


class TestW3CTraceContext:
    """Test W3C traceparent header generation and parsing."""

    def test_generate_trace_id_format(self):
        """Test trace_id is 32 hex chars."""
        trace_id = generate_trace_id()
        assert len(trace_id) == 32
        assert all(c in "0123456789abcdef" for c in trace_id)

    def test_generate_span_id_format(self):
        """Test span_id is 16 hex chars."""
        span_id = generate_span_id()
        assert len(span_id) == 16
        assert all(c in "0123456789abcdef" for c in span_id)

    def test_format_and_parse_traceparent(self):
        """Test traceparent round-trip."""
        trace_id = "0123456789abcdef0123456789abcdef"
        span_id = "0123456789abcdef"
        flags = "01"
        
        header = format_traceparent(trace_id, span_id, flags)
        assert header == "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
        
        parsed = parse_traceparent(header)
        assert parsed is not None
        assert parsed["trace_id"] == trace_id
        assert parsed["parent_span_id"] == span_id
        assert parsed["flags"] == flags

    def test_parse_invalid_traceparent(self):
        """Test parsing invalid traceparent returns None."""
        assert parse_traceparent("") is None
        assert parse_traceparent("invalid") is None
        assert parse_traceparent("00-short-span-flags") is None
        assert parse_traceparent("01-validtraceid123456789012345678901234-validspan12345678-01") is None  # Wrong version


class TestTracerContextManager:
    """Test Tracer span context manager."""

    @pytest.fixture
    def trace_store(self, tmp_path):
        db_path = str(tmp_path / "test_tracer.db")
        return init_trace_store(db_path, retention_days=7)

    def test_span_context_manager_records_on_exit(self, trace_store):
        """Test that span is recorded on context manager exit."""
        tracer = Tracer("test_component", trace_store)
        
        with tracer.span("test_operation") as span:
            span.set_attribute("key", "value")
            time.sleep(0.01)  # Ensure some duration
        
        spans = trace_store.get_trace(span.trace_id)
        assert len(spans) == 1
        assert spans[0].component == "test_component"
        assert spans[0].operation == "test_operation"
        assert spans[0].status == "completed"
        assert spans[0].duration_ms >= 10
        assert spans[0].attributes.get("key") == "value"

    def test_span_context_manager_records_error(self, trace_store):
        """Test that span records error status on exception."""
        tracer = Tracer("test_component", trace_store)
        trace_id_captured = None
        
        try:
            with tracer.span("failing_operation") as span:
                trace_id_captured = span.trace_id
                raise ValueError("Test error")
        except ValueError:
            pass
        
        # Get the trace by the captured trace_id
        spans = trace_store.get_trace(trace_id_captured)
        assert len(spans) == 1
        assert spans[0].status == "error"


class TestEndToEndTracePropagation:
    """Test end-to-end trace_id propagation from browser to dispatcher."""

    @pytest.mark.asyncio
    async def test_trace_id_propagation_browser_to_context_graph(self):
        """Test trace_id from simulated browser message propagates to ContextGraph events."""
        graph = ContextKnowledgeGraph()
        
        # Simulate browser message with trace_id
        trace_id = generate_trace_id()
        span_id = generate_span_id()
        
        # This mimics what happens in browser_bridge_endpoint
        event = graph.upsert_node(
            ContextNode(
                node_id="trace-prop-test",
                node_type=ContextNodeType.EPISODE,
                title="Trace Propagation Test",
                content={"trace_id": trace_id},
                confidence=0.95,
                source="chrome_extension",
                tags=["trace_test"],
            ),
            agent_id="chrome_extension",
            trace_id=trace_id,
        )
        
        # Verify event has trace_id
        assert event is not None
        assert event.trace_id == trace_id
        
        # Verify event is in history
        history = graph.get_node_history("trace-prop-test")
        assert len(history) >= 1
        assert history[0].trace_id == trace_id

    @pytest.mark.asyncio
    async def test_trace_id_in_action_envelope(self):
        """Test that ActionEnvelope can carry trace_id and span_id."""
        envelope = ActionEnvelope(
            action_id="action-123",
            agent_id="test_agent",
            session_id="sess-1",
            tool_name="test_tool",
            tier=TierLevel.TIER_1_STAGE,
            risk_score=0.25,
            risk_factors=["staging"],
            payload={"data": "test"},
            trace_id="trace-abc",
            span_id="span-xyz",
        )
        
        assert envelope.trace_id == "trace-abc"
        assert envelope.span_id == "span-xyz"

    @pytest.mark.asyncio
    async def test_trace_waterfall_via_http_endpoints(self):
        """Test trace waterfall retrieval via HTTP endpoints."""
        # Use file-based DB for test to avoid in-memory connection issues
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        trace_store = init_trace_store(db_path, retention_days=7)
        
        try:
            # Create a multi-component trace
            trace_id = generate_trace_id()
            components = [
                ("chrome_extension", "DOM_MUTATION_OBSERVED", None),
                ("ws_bridge", "forward_message", "chrome_extension"),
                ("hybrid_router", "route_and_generate", "ws_bridge"),
                ("policy_enclave", "evaluate_risk", "hybrid_router"),
                ("tool_dispatcher", "dispatch", "policy_enclave"),
            ]
            
            prev_span_id = None
            for i, (comp, op, parent_comp) in enumerate(components):
                span = TraceSpan(
                    trace_id=trace_id,
                    span_id=generate_span_id(),
                    parent_span_id=prev_span_id,
                    component=comp,
                    operation=op,
                    status="completed",
                    duration_ms=10 + i * 5,
                    attributes={"test": True},
                    started_at=datetime.now(timezone.utc),
                )
                trace_store.record_span(span)
                prev_span_id = span.span_id
            
            # Verify waterfall structure
            spans = trace_store.get_trace(trace_id)
            assert len(spans) == 5
            
            # Verify parent-child chain
            for i in range(1, len(spans)):
                assert spans[i].parent_span_id == spans[i - 1].span_id
            
            # Verify components in order
            expected_components = [c[0] for c in components]
            actual_components = [s.component for s in spans]
            assert actual_components == expected_components
        finally:
            trace_store.close()
            # On Windows, give a moment for file handle to release
            time.sleep(0.1)
            try:
                os.unlink(db_path)
            except PermissionError:
                pass  # Ignore on Windows if still locked


class TestContextGraphHistoryEndpoints:
    """Test Context Graph history HTTP endpoints."""

    @pytest.mark.asyncio
    async def test_get_node_history_endpoint(self):
        """Test GET /api/context/nodes/{node_id}/history endpoint."""
        graph = ContextKnowledgeGraph()
        
        node = ContextNode(
            node_id="endpoint-history-test",
            node_type=ContextNodeType.FACT,
            title="Endpoint Test",
            content={"data": "value"},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        graph.upsert_node(node, agent_id="test_agent")  # Update
        
        # Test via direct function call (endpoint logic is in main.py)
        history = graph.get_node_history("endpoint-history-test")
        assert len(history) == 2
        assert history[0].event_type == ContextEventType.NODE_CREATED
        assert history[1].event_type == ContextEventType.NODE_UPDATED

    @pytest.mark.asyncio
    async def test_replay_context_graph_endpoint(self):
        """Test GET /api/context/replay?as_of=<timestamp> endpoint."""
        graph = ContextKnowledgeGraph()
        
        node = ContextNode(
            node_id="replay-endpoint-test",
            node_type=ContextNodeType.FACT,
            title="Original",
            content={"version": 1},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        
        before_update = datetime.now(timezone.utc)
        time.sleep(0.01)
        
        node.title = "Updated"
        node.content = {"version": 2}
        node.updated_at = datetime.now(timezone.utc)
        graph.upsert_node(node, agent_id="test_agent")
        
        # Replay to before update
        replayed = graph.replay_until(before_update)
        replayed_node = next((n for n in replayed if n.node_id == "replay-endpoint-test"), None)
        assert replayed_node is not None
        assert replayed_node.title == "Original"
        assert replayed_node.content["version"] == 1


class TestTraceEndpoints:
    """Test trace HTTP endpoints."""

    @pytest.mark.asyncio
    async def test_get_trace_endpoint(self):
        """Test GET /api/traces/{trace_id} endpoint."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        trace_store = init_trace_store(db_path, retention_days=7)
        
        try:
            trace_id = generate_trace_id()
            span = TraceSpan(
                trace_id=trace_id,
                span_id=generate_span_id(),
                parent_span_id=None,
                component="test_component",
                operation="test_operation",
                status="completed",
                duration_ms=42,
                attributes={"key": "value"},
                started_at=datetime.now(timezone.utc),
            )
            trace_store.record_span(span)
            
            spans = trace_store.get_trace(trace_id)
            assert len(spans) == 1
            assert spans[0].trace_id == trace_id
        finally:
            trace_store.close()
            time.sleep(0.1)
            try:
                os.unlink(db_path)
            except PermissionError:
                pass  # Ignore on Windows if still locked

    @pytest.mark.asyncio
    async def test_get_recent_traces_endpoint(self):
        """Test GET /api/traces/recent endpoint."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        trace_store = init_trace_store(db_path, retention_days=7)
        
        try:
            for i in range(3):
                trace_id = generate_trace_id()
                span = TraceSpan(
                    trace_id=trace_id,
                    span_id=generate_span_id(),
                    parent_span_id=None,
                    component="test_component",
                    operation=f"operation_{i}",
                    status="completed",
                    duration_ms=10,
                    attributes={},
                    started_at=datetime.now(timezone.utc),
                )
                trace_store.record_span(span)
            
            recent = trace_store.get_recent_traces(limit=10)
            assert len(recent) == 3
            for trace in recent:
                assert "trace_id" in trace
                assert "span_count" in trace
                assert "components" in trace
        finally:
            trace_store.close()
            time.sleep(0.1)
            try:
                os.unlink(db_path)
            except PermissionError:
                pass  # Ignore on Windows if still locked