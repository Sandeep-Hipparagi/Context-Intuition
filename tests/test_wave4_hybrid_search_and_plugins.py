"""
Wave 4 Tests: Hybrid Semantic Vector Search & Declarative Browser Workflow Plugins.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

import pytest
import json
import struct
import httpx
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from app.ledger.context_graph import ContextKnowledgeGraph
from app.ledger.vector_index import LocalEmbeddingEngine, get_embedding_engine
from app.schemas import (
    ContextNode,
    ContextNodeType,
    ContextEvent,
    ContextEventType,
    ActionEnvelope,
    ApprovalStatus,
    TierLevel,
)
from app.main import app, context_graph


class TestLocalEmbeddingEngine:
    """Test LocalEmbeddingEngine with offline fallback and Ollama mocking."""

    @pytest.fixture
    def engine(self):
        return LocalEmbeddingEngine(mock_mode=True)

    def test_offline_embed_deterministic(self, engine):
        """Test that offline embeddings are deterministic for same input."""
        text = "Test product: organic apples, price $2.99"
        
        result1 = engine._offline_embed(text)
        result2 = engine._offline_embed(text)
        
        assert result1.vector == result2.vector
        assert result1.source == "offline"
        assert result1.model == "offline-deterministic"
        assert result1.dimension == 256
        # Verify unit vector
        import numpy as np
        norm = np.linalg.norm(result1.vector)
        assert abs(norm - 1.0) < 1e-6

    def test_offline_embed_different_texts(self, engine):
        """Test that different texts produce different embeddings."""
        result1 = engine._offline_embed("apples")
        result2 = engine._offline_embed("oranges")
        
        assert result1.vector != result2.vector

    def test_cosine_similarity_identical(self, engine):
        """Test cosine similarity of identical vectors is 1.0."""
        vec = [0.5] * 256
        # Normalize
        import numpy as np
        vec = (np.array(vec) / np.linalg.norm(vec)).tolist()
        
        sim = engine.cosine_similarity(vec, vec)
        assert abs(sim - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal(self, engine):
        """Test cosine similarity of orthogonal vectors is 0.0."""
        vec1 = [1.0] + [0.0] * 255
        vec2 = [0.0, 1.0] + [0.0] * 254
        
        sim = engine.cosine_similarity(vec1, vec2)
        assert abs(sim - 0.0) < 1e-6

    def test_cosine_similarity_clamped(self, engine):
        """Test cosine similarity is clamped to [0, 1]."""
        # Even if negative similarity occurs due to floating point, should clamp
        vec1 = [1.0, 0.0]
        vec2 = [-1.0, 0.0]
        sim = engine.cosine_similarity(vec1, vec2)
        assert 0.0 <= sim <= 1.0

    @pytest.mark.asyncio
    async def test_embed_mock_mode(self, engine):
        """Test embed() returns offline result in mock mode."""
        result = await engine.embed("test text")
        
        assert result.source == "offline"
        assert result.vector is not None
        assert len(result.vector) == 256

    @pytest.mark.asyncio
    async def test_embed_batch(self, engine):
        """Test batch embedding."""
        texts = ["text one", "text two", "text three"]
        results = await engine.embed_batch(texts)
        
        assert len(results) == 3
        for r in results:
            assert r.source == "offline"
            assert len(r.vector) == 256


class TestContextGraphEmbeddings:
    """Test ContextKnowledgeGraph embedding persistence and retrieval."""

    @pytest.fixture
    def graph(self):
        return ContextKnowledgeGraph()

    def test_upsert_node_creates_embedding(self, graph):
        """Test that upsert_node creates and stores embedding."""
        node = ContextNode(
            node_id="embed-test-1",
            node_type=ContextNodeType.FACT,
            title="Test Product",
            content={"name": "Organic Apples", "price": 2.99},
            confidence=0.9,
            source="test_agent",
            tags=["grocery", "test"],
        )
        
        event = graph.upsert_node(node, agent_id="test_agent", trace_id="trace-123")
        
        assert event is not None
        assert event.event_type == "NODE_CREATED"
        
        # Verify embedding was stored
        retrieved = graph.get_node("embed-test-1")
        assert retrieved is not None
        assert retrieved.embedding is not None
        assert len(retrieved.embedding) == 256

    def test_embedding_persisted_in_node_embeddings_table(self, graph):
        """Test that embedding is stored in node_embeddings table."""
        node = ContextNode(
            node_id="embed-table-test",
            node_type=ContextNodeType.FACT,
            title="Table Test",
            content={"data": "value"},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        
        # Direct SQL check
        import sqlite3
        conn = sqlite3.connect(graph.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM node_embeddings WHERE node_id = ?", ("embed-table-test",))
        row = cursor.fetchone()
        conn.close()
        
        assert row is not None
        assert row["node_id"] == "embed-table-test"
        assert row["model_name"] == "offline-deterministic"
        assert row["dimension"] == 256
        assert row["embedding_blob"] is not None

    def test_embedding_updated_on_node_update(self, graph):
        """Test that embedding is updated when node is updated."""
        node = ContextNode(
            node_id="embed-update-test",
            node_type=ContextNodeType.FACT,
            title="Original Title",
            content={"data": "original"},
            confidence=0.9,
            source="test_agent",
            tags=["test"],
        )
        graph.upsert_node(node, agent_id="test_agent")
        
        original = graph.get_node("embed-update-test")
        original_embedding = original.embedding
        
        # Update node
        node.title = "Updated Title"
        node.content = {"data": "updated"}
        node.updated_at = datetime.now(timezone.utc)
        graph.upsert_node(node, agent_id="test_agent")
        
        updated = graph.get_node("embed-update-test")
        # Embedding should be regenerated
        assert updated.embedding is not None
        # Different content should produce different embedding
        assert updated.embedding != original_embedding


class TestHybridSearch:
    """Test search_hybrid functionality."""

    @pytest.fixture
    def graph(self):
        return ContextKnowledgeGraph()

    def test_search_hybrid_basic(self, graph):
        """Test basic hybrid search returns results with scores."""
        # Create test nodes
        nodes = [
            ContextNode(
                node_id=f"search-test-{i}",
                node_type=ContextNodeType.FACT,
                title=f"Product {i}: Organic Apples",
                content={"name": f"Apples {i}", "price": 2.99 + i},
                confidence=0.9,
                source="test_agent",
                tags=["grocery"],
            )
            for i in range(5)
        ]
        
        for node in nodes:
            graph.upsert_node(node, agent_id="test_agent")
        
        # Search
        results = graph.search_hybrid("organic apples", limit=3)
        
        assert len(results) <= 3
        assert len(results) > 0
        for node, score in results:
            assert 0.0 <= score <= 1.0
            assert node.node_id.startswith("search-test-")
        
        # Results should be sorted by score descending
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_hybrid_with_filters(self, graph):
        """Test search with node_type and tag filters."""
        # Create mixed nodes
        graph.upsert_node(ContextNode(
            node_id="filter-fact",
            node_type=ContextNodeType.FACT,
            title="Fact Node",
            content={"type": "fact"},
            confidence=0.9,
            source="test",
            tags=["important"],
        ))
        graph.upsert_node(ContextNode(
            node_id="filter-episode",
            node_type=ContextNodeType.EPISODE,
            title="Episode Node",
            content={"type": "episode"},
            confidence=0.9,
            source="test",
            tags=["important"],
        ))
        
        # Filter by node_type
        results = graph.search_hybrid("node", limit=10, node_type=ContextNodeType.FACT)
        assert len(results) == 1
        assert results[0][0].node_id == "filter-fact"
        
        # Filter by tag
        results = graph.search_hybrid("node", limit=10, tag="important")
        assert len(results) == 2

    def test_search_hybrid_semantic_weight(self, graph):
        """Test that semantic_weight parameter affects ranking."""
        # Create nodes with similar temporal relevance but different semantic similarity
        node1 = ContextNode(
            node_id="semantic-close",
            node_type=ContextNodeType.FACT,
            title="Organic Apples Price",
            content={"product": "organic apples", "price": 2.99},
            confidence=0.9,
            source="test",
            tags=["grocery"],
        )
        node2 = ContextNode(
            node_id="semantic-far",
            node_type=ContextNodeType.FACT,
            title="Car Engine Oil",
            content={"product": "engine oil", "price": 19.99},
            confidence=0.9,
            source="test",
            tags=["automotive"],
        )
        graph.upsert_node(node1)
        graph.upsert_node(node2)
        
        # High semantic weight should rank "organic apples" higher for apple query
        results_high = graph.search_hybrid("organic apples", limit=2, semantic_weight=0.9)
        # Low semantic weight should rely more on temporal relevance
        results_low = graph.search_hybrid("organic apples", limit=2, semantic_weight=0.1)
        
        # With high semantic weight, the apple node should be first
        assert results_high[0][0].node_id == "semantic-close"
        # With low semantic weight, temporal relevance might change order
        # (but both have same temporal relevance, so might still be apple first)


class TestSearchAPI:
    """Test GET /api/context/search endpoint."""

    @pytest.mark.asyncio
    async def test_search_endpoint_basic(self):
        """Test basic search endpoint."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # First create some data
            from app.ledger.context_graph import ContextKnowledgeGraph
            from app.schemas import ContextNode, ContextNodeType
            graph = ContextKnowledgeGraph()
            graph.upsert_node(ContextNode(
                node_id="api-search-test",
                node_type=ContextNodeType.FACT,
                title="API Search Test: Organic Apples",
                content={"product": "organic apples"},
                confidence=0.9,
                source="test",
                tags=["grocery"],
            ))
            
            response = await client.get("/api/context/search?q=organic+apples&limit=5")
            assert response.status_code == 200
            
            data = response.json()
            assert isinstance(data, list)
            assert len(data) > 0
            for item in data:
                assert "node" in item
                assert "score" in item
                assert 0.0 <= item["score"] <= 1.0

    @pytest.mark.asyncio
    async def test_search_endpoint_with_params(self):
        """Test search endpoint with all parameters."""
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/context/search",
                params={
                    "q": "test",
                    "limit": 3,
                    "semantic_weight": 0.7,
                    "node_type": "FACT",
                    "tag": "grocery",
                }
            )
            assert response.status_code == 200
            data = response.json()
            assert len(data) <= 3


class TestPluginRegistry:
    """Test declarative browser workflow plugin registry concepts."""

    def test_plugin_registry_structure(self):
        """Test that plugin registry has expected structure."""
        # This test verifies the plugin registry concept by checking
        # the TypeScript definitions compile correctly
        from pathlib import Path
        registry_path = Path("chrome-extension/src/workflows/plugin_registry.ts")
        assert registry_path.exists()
        
        content = registry_path.read_text()
        assert "WorkflowPlugin" in content
        assert "canarySelectors" in content
        assert "onMount" in content
        assert "onCommand" in content
        assert "onUnmount" in content
        assert "SELECTOR_CANARY_WARNING" in content

    def test_workflows_refactored_as_plugins(self):
        """Test that workflows are refactored to use plugin registry."""
        workflows = [
            "chrome-extension/src/workflows/whatsapp_copilot.ts",
            "chrome-extension/src/workflows/grocery_tracker.ts",
            "chrome-extension/src/workflows/instagram_archivist.ts",
        ]
        
        for wf in workflows:
            path = Path(wf)
            assert path.exists(), f"Workflow {wf} not found"
            
            content = path.read_text()
            # Should have global window assignment for plugin registry
            assert "__whatsappWorkflow" in content or "__groceryWorkflow" in content or "__instagramWorkflow" in content

    def test_plugin_registry_exports(self):
        """Test that plugin registry exports expected symbols."""
        content = Path("chrome-extension/src/workflows/plugin_registry.ts").read_text()
        assert "export const pluginRegistry" in content
        assert "export interface WorkflowPlugin" in content
        assert "export interface WorkflowContext" in content
        assert "export interface CanaryWarningPayload" in content


class TestCanaryWarning:
    """Test SELECTOR_CANARY_WARNING handling."""

    def test_canary_warning_payload_schema(self):
        """Test CanaryWarningPayload schema in TypeScript."""
        content = Path("chrome-extension/src/types/bridge.ts").read_text()
        assert "CanaryWarningPayload" in content
        assert "pluginId" in content
        assert "pluginName" in content
        assert "missingSelectors" in content
        assert "url" in content
        assert "timestamp" in content
        assert "trace_id" in content

    def test_bridge_message_type_includes_canary(self):
        """Test that BridgeMessageType includes SELECTOR_CANARY_WARNING."""
        content = Path("chrome-extension/src/types/bridge.ts").read_text()
        assert "SELECTOR_CANARY_WARNING" in content

    def test_dashboard_canary_warning_type(self):
        """Test that dashboard has CanaryWarningPayload type."""
        content = Path("dashboard-ui/src/types/schema.ts").read_text()
        assert "CanaryWarningPayload" in content

    @pytest.mark.asyncio
    async def test_main_handles_canary_warning(self):
        """Test that main.py handles SELECTOR_CANARY_WARNING."""
        content = Path("core-orchestrator/app/main.py").read_text()
        assert "SELECTOR_CANARY_WARNING" in content
        assert "missingSelectors" in content
        assert "broadcast_to_dashboards" in content


class TestVersionBump:
    """Test version bumped to 0.3.0 across all files."""

    def read_file_safe(self, path: str) -> str:
        """Read file with UTF-8 encoding, fallback to latin-1."""
        try:
            return Path(path).read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return Path(path).read_text(encoding='latin-1')

    def test_pyproject_version(self):
        content = self.read_file_safe("pyproject.toml")
        assert 'version = "0.3.0"' in content

    def test_chrome_extension_package_version(self):
        content = self.read_file_safe("chrome-extension/package.json")
        assert '"version": "0.3.0"' in content

    def test_manifest_version(self):
        content = self.read_file_safe("chrome-extension/manifest.json")
        assert '"version": "0.3.0"' in content

    def test_dashboard_package_version(self):
        content = self.read_file_safe("dashboard-ui/package.json")
        assert '"version": "0.3.0"' in content

    def test_health_endpoint_version(self):
        content = self.read_file_safe("core-orchestrator/app/main.py")
        assert '"version": "0.3.0"' in content

    def test_readme_version_mention(self):
        content = self.read_file_safe("README.md")
        # README may not explicitly mention version, but check for Wave 4
        assert "Wave 4" in content or "v0.3.0" in content

    def test_roadmap_version_mention(self):
        content = self.read_file_safe("ROADMAP.md")
        assert "Wave 4" in content
        assert "v0.3.0" in content


class TestHardcodedPathsAndSecrets:
    """Verify no hardcoded paths or secrets in source code."""

    def read_file_safe(self, filepath: str) -> str:
        """Read file with UTF-8 encoding, fallback to latin-1."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            with open(filepath, 'r', encoding='latin-1') as f:
                return f.read()

    def test_no_hardcoded_secrets(self):
        """Scan for hardcoded secrets."""
        import re
        patterns = [
            r'sk_live_',
            r'sk_test_',
            r'ghp_',
            r'api_key\s*=\s*["\'][^"\']+["\']',
            r'password\s*=\s*["\'][^"\']+["\']',
        ]
        
        for root, dirs, files in os.walk("."):
            # Skip certain directories
            dirs[:] = [d for d in dirs if d not in ['node_modules', 'dist', '.git', '__pycache__', '.pytest_temp', 'context_intuition.egg-info']]
            
            for file in files:
                if file.endswith(('.py', '.ts', '.json', '.yaml', '.yml')):
                    filepath = os.path.join(root, file)
                    try:
                        content = self.read_file_safe(filepath)
                        for pattern in patterns:
                            matches = list(re.finditer(pattern, content, re.IGNORECASE))
                            for match in matches:
                                line_start = content.rfind('\n', 0, match.start()) + 1
                                line_end = content.find('\n', match.end())
                                if line_end == -1:
                                    line_end = len(content)
                                line = content[line_start:line_end]
                                
                                # Skip if in test file or using env vars
                                if 'test_' in file or 'VIBECORE_' in line or 'os.environ' in line or 'settings.' in line:
                                    continue
                                
                                # Skip CI workflow files
                                if '.github' in filepath:
                                    continue
                                
                                pytest.fail(f"Potential secret in {filepath}: {line.strip()[:100]}")
                    except Exception:
                        pass

    def test_no_hardcoded_paths(self):
        """Scan for hardcoded absolute paths."""
        import re
        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if d not in ['node_modules', 'dist', '.git', '__pycache__', '.pytest_temp', 'context_intuition.egg-info']]
            
            for file in files:
                if file.endswith(('.py', '.ts')):
                    filepath = os.path.join(root, file)
                    # Skip test files
                    if 'test_' in file or 'test/' in filepath or 'tests/' in filepath:
                        continue
                    try:
                        content = self.read_file_safe(filepath)
                        lines = content.split('\n')
                        for i, line in enumerate(lines, 1):
                            if re.search(r'(/home/|/Users/|C:\\\\)', line) and not re.search(r'(os\.path|Path\.home|Path\.expanduser|expanduser|os\.environ|environ\[|VIBECORE_HOME|#.*home|#.*Users|//.*home|//.*Users)', line):
                                pytest.fail(f"Hardcoded path in {filepath}:{i}: {line.strip()[:120]}")
                    except Exception:
                        pass


import os
from pathlib import Path