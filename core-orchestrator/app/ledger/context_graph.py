"""
Longitudinal Context Intuition Engine - Temporal Knowledge Graph & State Ledger.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Combines graph relations with temporal decay, trajectory tracking,
automated nightly consolidation, event-sourced audit trail,
and hybrid semantic + temporal search.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

from app.config import get_db_path
from app.ledger.vector_index import get_embedding_engine
from app.schemas import (
    ContextEdge,
    ContextEvent,
    ContextEventType,
    ContextNode,
    ContextNodeType,
)

logger = logging.getLogger("ContextGraph")


class ContextKnowledgeGraph:
    """
    SQLite-backed temporal state ledger for persistent user context,
    preferences, interaction episodes, and high-level strategic trajectories.
    Implements event-sourced audit trail with immutable context_events table.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(get_db_path("context_ledger.db"))
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            # Materialized view: current state of nodes
            conn.execute("""
                CREATE TABLE IF NOT EXISTS context_nodes (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT,
                    confidence REAL NOT NULL,
                    decay_rate REAL NOT NULL,
                    access_count INTEGER NOT NULL,
                    last_accessed TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    tags TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS context_edges (
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    weight REAL NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, target_id, relation),
                    FOREIGN KEY (source_id) REFERENCES context_nodes(node_id) ON DELETE CASCADE,
                    FOREIGN KEY (target_id) REFERENCES context_nodes(node_id) ON DELETE CASCADE
                )
            """)
            # Event store: immutable append-only log of all mutations
            conn.execute("""
                CREATE TABLE IF NOT EXISTS context_events (
                    event_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    delta_payload TEXT NOT NULL,
                    agent_id TEXT,
                    trace_id TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_context_events_node_id
                ON context_events(node_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_context_events_trace_id
                ON context_events(trace_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_context_events_created_at
                ON context_events(created_at)
            """)
            # Embedding store: persistent vector embeddings for hybrid search
            conn.execute("""
                CREATE TABLE IF NOT EXISTS node_embeddings (
                    node_id TEXT PRIMARY KEY,
                    embedding_blob BLOB NOT NULL,
                    model_name TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (node_id) REFERENCES context_nodes(node_id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_node_embeddings_model
                ON node_embeddings(model_name)
            """)
            conn.commit()

    def _append_event(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        event_type: ContextEventType,
        delta_payload: dict[str, Any],
        agent_id: str | None = None,
        trace_id: str | None = None,
    ) -> ContextEvent:
        """Append an immutable event to the event store within an existing transaction."""
        event = ContextEvent(
            node_id=node_id,
            event_type=event_type,
            delta_payload=delta_payload,
            agent_id=agent_id,
            trace_id=trace_id,
        )
        conn.execute(
            """
            INSERT INTO context_events (
                event_id, node_id, event_type, delta_payload,
                agent_id, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.node_id,
                event.event_type.value,
                json.dumps(event.delta_payload),
                event.agent_id,
                event.trace_id,
                event.created_at.isoformat(),
            ),
        )
        return event

    def upsert_node(
        self,
        node: ContextNode,
        agent_id: str | None = None,
        trace_id: str | None = None,
    ) -> ContextEvent:
        """
        Upsert a node and append a NODE_CREATED or NODE_UPDATED event.
        Generates and persists vector embedding for hybrid search.
        Returns the created event for audit trail.
        """
        now_str = datetime.now(UTC).isoformat()
        is_new = False

        # Prepare text for embedding: combine title and content summary
        embed_text = f"{node.title}. {json.dumps(node.content)[:500]}"

        # Generate embedding
        embedding_engine = get_embedding_engine()
        embedding_result = embedding_engine._offline_embed(embed_text)  # Use sync offline for now
        embedding_vector = embedding_result.vector
        model_name = embedding_result.model
        dimension = embedding_result.dimension

        # Convert embedding to JSON for context_nodes table
        embedding_json = json.dumps(embedding_vector)
        # Convert to binary for node_embeddings table
        embedding_blob = struct.pack(f"{dimension}f", *embedding_vector)

        with self._get_connection() as conn:
            # Check if node exists and preserve original created_at timestamp
            cursor = conn.cursor()
            cursor.execute("SELECT created_at FROM context_nodes WHERE node_id = ?", (node.node_id,))
            existing = cursor.fetchone()
            is_new = existing is None
            created_at_str = node.created_at.isoformat() if is_new else existing[0]

            event_type = ContextEventType.NODE_CREATED if is_new else ContextEventType.NODE_UPDATED

            conn.execute(
                """
                INSERT OR REPLACE INTO context_nodes (
                    node_id, node_type, title, content, embedding,
                    confidence, decay_rate, access_count, last_accessed,
                    created_at, updated_at, source, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.node_type.value,
                    node.title,
                    json.dumps(node.content),
                    embedding_json,
                    node.confidence,
                    node.decay_rate,
                    node.access_count,
                    node.last_accessed.isoformat(),
                    created_at_str,
                    now_str,
                    node.source,
                    json.dumps(node.tags),
                ),
            )
            # Upsert edges
            for edge in node.edges:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO context_edges (
                        source_id, target_id, relation, weight, metadata, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge.source_id,
                        edge.target_id,
                        edge.relation,
                        edge.weight,
                        json.dumps(edge.metadata),
                        edge.created_at.isoformat(),
                    ),
                )

            # Store embedding in node_embeddings table
            conn.execute(
                """
                INSERT OR REPLACE INTO node_embeddings (
                    node_id, embedding_blob, model_name, dimension, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    embedding_blob,
                    model_name,
                    dimension,
                    now_str,
                ),
            )

            # Append event
            delta = {
                "node_type": node.node_type.value,
                "title": node.title,
                "content": node.content,
                "confidence": node.confidence,
                "decay_rate": node.decay_rate,
                "access_count": node.access_count,
                "source": node.source,
                "tags": node.tags,
            }
            event = self._append_event(conn, node.node_id, event_type, delta, agent_id, trace_id)
            conn.commit()

        return event

    def get_node(self, node_id: str) -> ContextNode | None:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM context_nodes WHERE node_id = ?", (node_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_node(conn, row)

    def find_nodes(
        self,
        node_type: ContextNodeType | None = None,
        tag: str | None = None,
        min_relevance: float = 0.20,
    ) -> list[ContextNode]:
        results: list[ContextNode] = []
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = "SELECT * FROM context_nodes WHERE 1=1"
            params: list[Any] = []

            if node_type:
                query += " AND node_type = ?"
                params.append(node_type.value)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            for row in rows:
                node = self._row_to_node(conn, row)
                if tag and tag not in node.tags:
                    continue
                if node.compute_decayed_relevance() >= min_relevance:
                    results.append(node)

        # Sort by decayed relevance descending
        results.sort(key=lambda n: n.compute_decayed_relevance(), reverse=True)
        return results

    def touch_node(
        self,
        node_id: str,
        agent_id: str | None = None,
        trace_id: str | None = None,
    ) -> ContextEvent | None:
        """
        Signals that a node was referenced by an agent or user action,
        boosting its access count and resetting its decay curve.
        Returns the CONFIDENCE_REINFORCED event.
        """
        now_str = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            update_cursor = conn.execute(
                """
                UPDATE context_nodes
                SET access_count = access_count + 1, last_accessed = ?, updated_at = ?
                WHERE node_id = ?
                """,
                (now_str, now_str, node_id),
            )
            if update_cursor.rowcount == 0:
                return None

            # Get updated access_count for delta
            cursor = conn.cursor()
            cursor.execute("SELECT access_count, confidence FROM context_nodes WHERE node_id = ?", (node_id,))
            row = cursor.fetchone()
            if not row:
                return None

            delta = {
                "access_count": row["access_count"],
                "confidence": row["confidence"],
            }
            event = self._append_event(
                conn, node_id, ContextEventType.CONFIDENCE_REINFORCED, delta, agent_id, trace_id
            )
            conn.commit()
            return event

    def get_node_history(self, node_id: str) -> list[ContextEvent]:
        """
        Returns the chronological causal chain of mutations for a node.
        Immutable event log - never modified after creation.
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM context_events WHERE node_id = ? ORDER BY created_at ASC",
                (node_id,),
            )
            return [
                ContextEvent(
                    event_id=row["event_id"],
                    node_id=row["node_id"],
                    event_type=ContextEventType(row["event_type"]),
                    delta_payload=json.loads(row["delta_payload"]),
                    agent_id=row["agent_id"],
                    trace_id=row["trace_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in cursor.fetchall()
            ]

    def replay_until(self, as_of: datetime) -> list[ContextNode]:
        """
        Reconstructs the exact materialized graph state as it existed at a historical timestamp.
        Replays all events chronologically up to the given timestamp without mutating live tables.
        """
        with contextlib.closing(sqlite3.connect(":memory:")) as replay_conn:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Recreate schema in memory
                replay_conn.executescript("""
                    CREATE TABLE context_nodes (
                        node_id TEXT PRIMARY KEY,
                        node_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        embedding TEXT,
                        confidence REAL NOT NULL,
                        decay_rate REAL NOT NULL,
                        access_count INTEGER NOT NULL,
                        last_accessed TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        source TEXT NOT NULL,
                        tags TEXT NOT NULL
                    );
                    CREATE TABLE context_edges (
                        source_id TEXT NOT NULL,
                        target_id TEXT NOT NULL,
                        relation TEXT NOT NULL,
                        weight REAL NOT NULL,
                        metadata TEXT,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (source_id, target_id, relation),
                        FOREIGN KEY (source_id) REFERENCES context_nodes(node_id) ON DELETE CASCADE,
                        FOREIGN KEY (target_id) REFERENCES context_nodes(node_id) ON DELETE CASCADE
                    );
                """)

                # Replay events up to as_of
                as_of_str = as_of.isoformat()
                cursor.execute(
                    "SELECT * FROM context_events WHERE created_at <= ? ORDER BY created_at ASC",
                    (as_of_str,),
                )
                for row in cursor.fetchall():
                    event = ContextEvent(
                        event_id=row["event_id"],
                        node_id=row["node_id"],
                        event_type=ContextEventType(row["event_type"]),
                        delta_payload=json.loads(row["delta_payload"]),
                        agent_id=row["agent_id"],
                        trace_id=row["trace_id"],
                        created_at=datetime.fromisoformat(row["created_at"]),
                    )

                    delta = event.delta_payload
                    if event.event_type in (ContextEventType.NODE_CREATED, ContextEventType.NODE_UPDATED):
                        replay_conn.execute(
                            """
                            INSERT OR REPLACE INTO context_nodes (
                                node_id, node_type, title, content, embedding,
                                confidence, decay_rate, access_count, last_accessed,
                                created_at, updated_at, source, tags
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                event.node_id,
                                delta.get("node_type"),
                                delta.get("title", ""),
                                json.dumps(delta.get("content", {})),
                                None,  # embedding not stored in events
                                delta.get("confidence", 1.0),
                                delta.get("decay_rate", 0.05),
                                delta.get("access_count", 0),
                                event.created_at.isoformat(),
                                delta.get("created_at", event.created_at.isoformat()),
                                event.created_at.isoformat(),
                                delta.get("source", "replay"),
                                json.dumps(delta.get("tags", [])),
                            ),
                        )
                    elif event.event_type == ContextEventType.CONFIDENCE_REINFORCED:
                        replay_conn.execute(
                            """
                            UPDATE context_nodes
                            SET access_count = ?, confidence = ?, last_accessed = ?, updated_at = ?
                            WHERE node_id = ?
                            """,
                            (
                                delta.get("access_count", 0),
                                delta.get("confidence", 1.0),
                                event.created_at.isoformat(),
                                event.created_at.isoformat(),
                                event.node_id,
                            ),
                        )
                    elif event.event_type == ContextEventType.NODE_PRUNED:
                        replay_conn.execute("DELETE FROM context_nodes WHERE node_id = ?", (event.node_id,))

                # Now materialize the nodes from replay DB
                replay_conn.row_factory = sqlite3.Row
                replay_cursor = replay_conn.cursor()
                replay_cursor.execute("SELECT * FROM context_nodes")
                rows = replay_cursor.fetchall()
                nodes: list[ContextNode] = []

                for row in rows:
                    node = self._row_to_node(replay_conn, row)
                    nodes.append(node)

            return nodes

    def nightly_consolidation(self, decay_threshold: float = 0.15) -> dict[str, int]:
        """
        Runs overnight to:
        1. Evaluate decayed relevance across all nodes.
        2. Archive or prune stale, low-confidence facts (decay < threshold).
        3. Consolidate raw episodic nodes from the last 24h into a synthetic TRAJECTORY node.
        """
        now = datetime.now(UTC)
        pruned_count = 0
        consolidated_episodes = 0

        trajectory_node: ContextNode | None = None

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Retrieve all nodes
            cursor.execute("SELECT * FROM context_nodes")
            rows = cursor.fetchall()
            recent_episodes: list[ContextNode] = []

            for row in rows:
                node = self._row_to_node(conn, row)
                relevance = node.compute_decayed_relevance(as_of=now)

                # Collect recent episodes (< 24h old)
                hours_old = (now - node.created_at).total_seconds() / 3600.0
                if node.node_type == ContextNodeType.EPISODE and hours_old <= 24.0:
                    recent_episodes.append(node)

                # Prune cold episodic/fact nodes below threshold
                if relevance < decay_threshold and node.node_type in (ContextNodeType.EPISODE, ContextNodeType.FACT):
                    conn.execute("DELETE FROM context_nodes WHERE node_id = ?", (node.node_id,))
                    # Append NODE_PRUNED event
                    self._append_event(
                        conn,
                        node.node_id,
                        ContextEventType.NODE_PRUNED,
                        {"pruned_relevance": relevance, "reason": "decay_below_threshold"},
                        agent_id="nightly_consolidation",
                        trace_id=None,
                    )
                    pruned_count += 1

            # Synthesize trajectory node if recent episodes exist
            if recent_episodes:
                consolidated_episodes = len(recent_episodes)
                trajectory_title = f"Consolidated Trajectory: {now.strftime('%Y-%m-%d')}"
                trajectory_summary = [
                    f"- [{ep.source}] {ep.title}: {json.dumps(ep.content)[:120]}"
                    for ep in recent_episodes
                ]
                trajectory_node = ContextNode(
                    node_id=str(uuid4()),
                    node_type=ContextNodeType.TRAJECTORY,
                    title=trajectory_title,
                    content={
                        "summary": "\n".join(trajectory_summary),
                        "episode_count": consolidated_episodes,
                        "date": now.strftime("%Y-%m-%d"),
                    },
                    confidence=1.0,
                    decay_rate=0.01,  # Trajectories decay very slowly
                    source="nightly_consolidation",
                    tags=["trajectory", "consolidated_summary"],
                )

            conn.commit()

        if trajectory_node:
            self.upsert_node(trajectory_node, agent_id="nightly_consolidation")

        logger.info(
            f" [CONSOLIDATION] Nightly pass complete: "
            f"{pruned_count} stale nodes pruned, {consolidated_episodes} episodes consolidated."
        )
        return {"pruned": pruned_count, "consolidated": consolidated_episodes}

    def search_hybrid(
        self,
        query: str,
        limit: int = 10,
        semantic_weight: float = 0.6,
        node_type: ContextNodeType | None = None,
        tag: str | None = None,
    ) -> list[tuple[ContextNode, float]]:
        """
        Hybrid semantic + temporal search.

        Computes cosine similarity between query embedding and node embeddings,
        combines with normalized temporal decayed relevance.

        hybrid_score = semantic_weight * semantic_similarity + (1 - semantic_weight) * normalized_decayed_relevance

        Args:
            query: Search query text
            limit: Maximum number of results
            semantic_weight: Weight for semantic similarity (0.0 to 1.0)
            node_type: Optional filter by node type
            tag: Optional filter by tag

        Returns:
            List of (ContextNode, hybrid_score) tuples sorted by score descending
        """
        # Generate query embedding
        embedding_engine = get_embedding_engine()
        query_embedding = embedding_engine._offline_embed(query).vector

        # Build candidate query
        where_clauses = []
        params: list[Any] = []

        if node_type:
            where_clauses.append("cn.node_type = ?")
            params.append(node_type.value)

        if tag:
            where_clauses.append("cn.tags LIKE ?")
            params.append(f'%{tag}%')

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Fetch candidates with their embeddings
        candidates: list[tuple[ContextNode, list[float]]] = []

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get nodes with embeddings (where_sql only joins static literals; values are parameterized)
            sql = (
                "SELECT cn.*, ne.embedding_blob, ne.dimension "  # noqa: S608
                "FROM context_nodes cn "
                "JOIN node_embeddings ne ON cn.node_id = ne.node_id "
                f"WHERE {where_sql}"  # nosec B608
            )
            cursor.execute(sql, params)
            rows = cursor.fetchall()

            for row in rows:
                # Reconstruct embedding from blob
                blob = row["embedding_blob"]
                dim = row["dimension"]
                embedding_vector = list(struct.unpack(f"{dim}f", blob))

                # Reconstruct node (without edges for efficiency)
                node = ContextNode(
                    node_id=row["node_id"],
                    node_type=ContextNodeType(row["node_type"]),
                    title=row["title"],
                    content=json.loads(row["content"]),
                    embedding=None,
                    confidence=row["confidence"],
                    decay_rate=row["decay_rate"],
                    access_count=row["access_count"],
                    last_accessed=datetime.fromisoformat(row["last_accessed"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                    source=row["source"],
                    tags=json.loads(row["tags"]),
                    edges=[],
                )
                candidates.append((node, embedding_vector))

        if not candidates:
            return []

        # Compute hybrid scores
        embedding_engine = get_embedding_engine()
        results: list[tuple[ContextNode, float]] = []

        # Find max decayed relevance for normalization
        max_relevance = max(n.compute_decayed_relevance() for n, _ in candidates) if candidates else 1.0

        for node, embedding in candidates:
            # Semantic similarity (cosine)
            semantic_sim = embedding_engine.cosine_similarity(query_embedding, embedding)

            # Normalized temporal relevance
            temporal_rel = node.compute_decayed_relevance()
            normalized_temporal = temporal_rel / max_relevance if max_relevance > 0 else 0.0

            # Hybrid score
            hybrid_score = semantic_weight * semantic_sim + (1.0 - semantic_weight) * normalized_temporal

            results.append((node, hybrid_score))

        # Sort by hybrid score descending
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:limit]

    def _row_to_node(self, conn: sqlite3.Connection, row: sqlite3.Row) -> ContextNode:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM context_edges WHERE source_id = ?", (row["node_id"],))
        edge_rows = cursor.fetchall()
        edges = [
            ContextEdge(
                source_id=e["source_id"],
                target_id=e["target_id"],
                relation=e["relation"],
                weight=e["weight"],
                metadata=json.loads(e["metadata"]) if e["metadata"] else {},
                created_at=datetime.fromisoformat(e["created_at"]),
            )
            for e in edge_rows
        ]

        return ContextNode(
            node_id=row["node_id"],
            node_type=ContextNodeType(row["node_type"]),
            title=row["title"],
            content=json.loads(row["content"]),
            embedding=json.loads(row["embedding"]) if row["embedding"] else None,
            confidence=row["confidence"],
            decay_rate=row["decay_rate"],
            access_count=row["access_count"],
            last_accessed=datetime.fromisoformat(row["last_accessed"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            source=row["source"],
            tags=json.loads(row["tags"]),
            edges=edges,
        )
