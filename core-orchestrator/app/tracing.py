"""
VibeCore Distributed Tracing - Local Span Store & W3C Trace Propagation.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Zero-dependency local trace storage with W3C traceparent compatibility.
Retains spans for 7 days with automatic pruning.
"""

from __future__ import annotations

import logging
import os
import random
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))

from app.config import get_vibecore_home

logger = logging.getLogger("Tracing")


@dataclass
class TraceSpan:
    """Single span in a distributed trace."""
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    component: str  # e.g., "chrome_extension", "ws_bridge", "hybrid_router", "policy_enclave", "tool_dispatcher"
    operation: str  # e.g., "DOM_MUTATION_OBSERVED", "route_and_generate", "evaluate_risk", "dispatch"
    status: str  # "started", "completed", "error"
    duration_ms: Optional[int] = None
    attributes: Dict[str, Any] = None
    started_at: datetime = None

    def __post_init__(self):
        if self.attributes is None:
            self.attributes = {}
        if self.started_at is None:
            self.started_at = datetime.now(timezone.utc)


class TraceStore:
    """
    Local SQLite-backed trace store with W3C traceparent compatibility.
    Automatic retention pruning (default 7 days).
    """

    def __init__(self, db_path: Optional[str] = None, retention_days: int = 7):
        if db_path is None:
            db_path = str(get_vibecore_home() / "traces.db")
        self.db_path = db_path
        self.retention_days = retention_days
        self._lock = threading.RLock()
        self._shared_conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        # For in-memory databases, we must reuse the same connection
        if self.db_path == ":memory:":
            if self._shared_conn is None:
                self._shared_conn = sqlite3.connect(":memory:", timeout=30.0, check_same_thread=False)
                self._shared_conn.execute("PRAGMA journal_mode = WAL;")
                self._shared_conn.execute("PRAGMA busy_timeout = 30000;")
                self._shared_conn.execute("PRAGMA synchronous = NORMAL;")
            return self._shared_conn
        
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trace_spans (
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    component TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms INTEGER,
                    attributes TEXT,
                    started_at TEXT NOT NULL,
                    PRIMARY KEY (trace_id, span_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trace_spans_trace_id
                ON trace_spans(trace_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trace_spans_started_at
                ON trace_spans(started_at)
            """)
            conn.commit()

    def record_span(self, span: TraceSpan) -> None:
        """Record a completed span."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO trace_spans (
                        trace_id, span_id, parent_span_id, component,
                        operation, status, duration_ms, attributes, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        span.trace_id,
                        span.span_id,
                        span.parent_span_id,
                        span.component,
                        span.operation,
                        span.status,
                        span.duration_ms,
                        json.dumps(span.attributes) if span.attributes else None,
                        span.started_at.isoformat(),
                    ),
                )
                conn.commit()

    def get_trace(self, trace_id: str) -> List[TraceSpan]:
        """Get all spans for a trace_id, ordered by start time."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at ASC",
                (trace_id,),
            )
            spans = []
            for row in cursor.fetchall():
                spans.append(TraceSpan(
                    trace_id=row["trace_id"],
                    span_id=row["span_id"],
                    parent_span_id=row["parent_span_id"],
                    component=row["component"],
                    operation=row["operation"],
                    status=row["status"],
                    duration_ms=row["duration_ms"],
                    attributes=json.loads(row["attributes"]) if row["attributes"] else {},
                    started_at=datetime.fromisoformat(row["started_at"]),
                ))
            return spans

    def get_recent_traces(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trace summaries (one per trace_id)."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT trace_id, MIN(started_at) as started_at,
                       COUNT(*) as span_count,
                       MAX(duration_ms) as max_duration_ms,
                       GROUP_CONCAT(DISTINCT component) as components
                FROM trace_spans
                GROUP BY trace_id
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            traces = []
            for row in cursor.fetchall():
                traces.append({
                    "trace_id": row["trace_id"],
                    "started_at": row["started_at"],
                    "span_count": row["span_count"],
                    "max_duration_ms": row["max_duration_ms"],
                    "components": row["components"].split(",") if row["components"] else [],
                })
            return traces

    def prune_old_spans(self) -> int:
        """Delete spans older than retention_days. Returns count deleted."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        cutoff_str = cutoff.isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM trace_spans WHERE started_at < ?",
                    (cutoff_str,),
                )
                conn.commit()
                return cursor.rowcount

    def close(self) -> None:
        """Close the shared connection (for in-memory databases)."""
        with self._lock:
            if self._shared_conn is not None:
                self._shared_conn.close()
                self._shared_conn = None


# Global trace store instance
_global_trace_store: Optional[TraceStore] = None
_trace_store_lock = threading.Lock()


def get_trace_store() -> TraceStore:
    global _global_trace_store
    with _trace_store_lock:
        if _global_trace_store is None:
            _global_trace_store = TraceStore()
        return _global_trace_store


def init_trace_store(db_path: Optional[str] = None, retention_days: int = 7) -> TraceStore:
    """Initialize or reinitialize the global trace store (for testing)."""
    global _global_trace_store
    with _trace_store_lock:
        _global_trace_store = TraceStore(db_path, retention_days)
        return _global_trace_store


# W3C Trace Context Helpers

def generate_trace_id() -> str:
    """Generate a 32-hex-char W3C-compatible trace_id."""
    return "".join(f"{random.getrandbits(8):02x}" for _ in range(16))


def generate_span_id() -> str:
    """Generate a 16-hex-char W3C-compatible span_id."""
    return "".join(f"{random.getrandbits(8):02x}" for _ in range(8))


def parse_traceparent(header: str) -> Optional[Dict[str, str]]:
    """
    Parse W3C traceparent header: version-trace-id-parent-id-flags
    Returns dict with trace_id, span_id (parent), flags, or None if invalid.
    """
    if not header:
        return None
    parts = header.split("-")
    if len(parts) != 4:
        return None
    version, trace_id, parent_id, flags = parts
    if version != "00":
        return None
    if len(trace_id) != 32 or len(parent_id) != 16:
        return None
    return {
        "trace_id": trace_id,
        "parent_span_id": parent_id,
        "flags": flags,
    }


def format_traceparent(trace_id: str, span_id: str, flags: str = "01") -> str:
    """Format W3C traceparent header."""
    return f"00-{trace_id}-{span_id}-{flags}"


class Tracer:
    """
    Context manager for creating spans with automatic timing and recording.
    Usage:
        with tracer.span("component", "operation", trace_id, parent_span_id) as span:
            # do work
            span.set_attribute("key", "value")
    """

    def __init__(self, component: str, trace_store: Optional[TraceStore] = None):
        self.component = component
        self.trace_store = trace_store or get_trace_store()

    def span(
        self,
        operation: str,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> "SpanContext":
        """Create a new span context manager."""
        if trace_id is None:
            trace_id = generate_trace_id()
        span_id = generate_span_id()
        return SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            component=self.component,
            operation=operation,
            attributes=attributes or {},
            trace_store=self.trace_store,
        )


class SpanContext:
    """Context manager for a single span with automatic start/end timing."""

    def __init__(
        self,
        trace_id: str,
        span_id: str,
        parent_span_id: Optional[str],
        component: str,
        operation: str,
        attributes: Dict[str, Any],
        trace_store: TraceStore,
    ):
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.component = component
        self.operation = operation
        self.attributes = attributes
        self.trace_store = trace_store
        self._start_time: Optional[float] = None
        self._span: Optional[TraceSpan] = None

    def __enter__(self) -> "SpanContext":
        self._start_time = time.perf_counter()
        self._span = TraceSpan(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            component=self.component,
            operation=self.operation,
            status="started",
            attributes=self.attributes,
            started_at=datetime.now(timezone.utc),
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._span is None:
            return
        duration_ms = int((time.perf_counter() - self._start_time) * 1000)
        self._span.duration_ms = duration_ms
        self._span.status = "error" if exc_type else "completed"
        self.trace_store.record_span(self._span)

    def set_attribute(self, key: str, value: Any) -> None:
        """Add an attribute to the current span."""
        if self._span:
            self._span.attributes[key] = value

    def get_traceparent(self) -> str:
        """Get W3C traceparent header for downstream propagation."""
        return format_traceparent(self.trace_id, self.span_id)


# Convenience functions for common components

def create_chrome_extension_tracer() -> Tracer:
    return Tracer("chrome_extension")


def create_ws_bridge_tracer() -> Tracer:
    return Tracer("ws_bridge")


def create_hybrid_router_tracer() -> Tracer:
    return Tracer("hybrid_router")


def create_policy_enclave_tracer() -> Tracer:
    return Tracer("policy_enclave")


def create_tool_dispatcher_tracer() -> Tracer:
    return Tracer("tool_dispatcher")


def create_context_graph_tracer() -> Tracer:
    return Tracer("context_graph")


import json