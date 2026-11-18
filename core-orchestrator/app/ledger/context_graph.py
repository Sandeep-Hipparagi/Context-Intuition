"""
Longitudinal Context Intuition Engine - Temporal Knowledge Graph & State Ledger.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Combines graph relations with temporal decay, trajectory tracking,
and automated nightly consolidation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional
from uuid import uuid4

import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

from app.config import get_db_path
from app.schemas import (
    ContextEdge,
    ContextNode,
    ContextNodeType,
)

logger = logging.getLogger("ContextGraph")


class ContextKnowledgeGraph:
    """
    SQLite-backed temporal state ledger for persistent user context,
    preferences, interaction episodes, and high-level strategic trajectories.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(get_db_path("context_ledger.db"))
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
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
            conn.commit()

    def upsert_node(self, node: ContextNode) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
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
                    json.dumps(node.embedding) if node.embedding else None,
                    node.confidence,
                    node.decay_rate,
                    node.access_count,
                    node.last_accessed.isoformat(),
                    node.created_at.isoformat(),
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
            conn.commit()

    def get_node(self, node_id: str) -> Optional[ContextNode]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM context_nodes WHERE node_id = ?", (node_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_node(conn, row)

    def find_nodes(
        self,
        node_type: Optional[ContextNodeType] = None,
        tag: Optional[str] = None,
        min_relevance: float = 0.20,
    ) -> List[ContextNode]:
        results: List[ContextNode] = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = "SELECT * FROM context_nodes WHERE 1=1"
            params: List[Any] = []

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

    def touch_node(self, node_id: str) -> None:
        """
        Signals that a node was referenced by an agent or user action,
        boosting its access count and resetting its decay curve.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE context_nodes
                SET access_count = access_count + 1, last_accessed = ?
                WHERE node_id = ?
                """,
                (now_str, node_id),
            )
            conn.commit()

    def nightly_consolidation(self, decay_threshold: float = 0.15) -> Dict[str, int]:
        """
        Runs overnight to:
        1. Evaluate decayed relevance across all nodes.
        2. Archive or prune stale, low-confidence facts (decay < threshold).
        3. Consolidate raw episodic nodes from the last 24h into a synthetic TRAJECTORY node.
        """
        now = datetime.now(timezone.utc)
        pruned_count = 0
        consolidated_episodes = 0

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Retrieve all nodes
            cursor.execute("SELECT * FROM context_nodes")
            rows = cursor.fetchall()
            recent_episodes: List[ContextNode] = []

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
                self.upsert_node(trajectory_node)

            conn.commit()

        logger.info(
            f" [CONSOLIDATION] Nightly pass complete: "
            f"{pruned_count} stale nodes pruned, {consolidated_episodes} episodes consolidated."
        )
        return {"pruned": pruned_count, "consolidated": consolidated_episodes}

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
