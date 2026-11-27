"""
Wave 1 Tests: HybridRouter production paths, PII scrubbing, ContextGraph decay,
EditDeltaEngine edge cases, and FrameDeduplicator.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.ledger.context_graph import ContextKnowledgeGraph
from app.ledger.edit_delta import EditDeltaEngine
from app.main import frame_deduplicator
from app.router.hybrid_router import (
    HybridRouter,
    PiiScrubber,
    get_hybrid_router,
    shutdown_hybrid_router,
)
from app.schemas import (
    ActionEnvelope,
    ContextNode,
    ContextNodeType,
    TierLevel,
)


class TestPiiScrubber:
    """Test PII scrubbing and restoration round-trips."""

    def test_scrub_emails(self):
        text = "Contact john.doe@example.com or jane@test.org for details."
        scrubbed, token_map = PiiScrubber.scrub(text)
        assert "[REDACTED_EMAIL_1]" in scrubbed
        assert "[REDACTED_EMAIL_2]" in scrubbed
        assert "john.doe@example.com" not in scrubbed
        assert "jane@test.org" not in scrubbed
        assert token_map["[REDACTED_EMAIL_1]"] == "john.doe@example.com"
        assert token_map["[REDACTED_EMAIL_2]"] == "jane@test.org"

    def test_scrub_phones(self):
        text = "Call +1-555-123-4567 or (555) 123-4567."
        scrubbed, _token_map = PiiScrubber.scrub(text)
        assert "[REDACTED_PHONE_1]" in scrubbed
        assert "[REDACTED_PHONE_2]" in scrubbed
        assert "+1-555-123-4567" not in scrubbed
        assert "(555) 123-4567" not in scrubbed

    def test_scrub_credit_cards(self):
        text = "Card: 4111 1111 1111 1111 and 4111-1111-1111-1111"
        scrubbed, _token_map = PiiScrubber.scrub(text)
        assert "[REDACTED_CARD_1]" in scrubbed
        assert "[REDACTED_CARD_2]" in scrubbed
        assert "4111 1111 1111 1111" not in scrubbed

    def test_restore_roundtrip(self):
        original = "Email john@test.com, call 555-123-4567, card 4111-1111-1111-1111"
        scrubbed, token_map = PiiScrubber.scrub(original)
        restored = PiiScrubber.restore(scrubbed, token_map)
        assert restored == original

    def test_multiple_types_together(self):
        text = "Contact john@test.com or call 555-123-4567. Card: 4111-1111-1111-1111"
        scrubbed, token_map = PiiScrubber.scrub(text)
        restored = PiiScrubber.restore(scrubbed, token_map)
        assert restored == text


class TestHybridRouterMockMode:
    """Test HybridRouter in mock mode (offline/CI)."""

    @pytest.fixture
    def router(self):
        return HybridRouter(mock_mode=True)

    @pytest.mark.asyncio
    async def test_mock_mode_local_tasks(self, router):
        result = await router.route_and_generate(
            prompt="Classify this message",
            task_type="classification",
        )
        assert result["routed_to"] == "local_ollama"
        assert "MOCK:local_ollama" in result["text"]

    @pytest.mark.asyncio
    async def test_mock_mode_strictly_local(self, router):
        for task_type in ["dom_parse", "whatsapp_classification", "pii_scrub", "financial_triage"]:
            result = await router.route_and_generate(
                prompt="Test prompt",
                task_type=task_type,
            )
            assert result["routed_to"] == "local_ollama"

    @pytest.mark.asyncio
    async def test_mock_mode_complex_reasoning_no_cloud(self, router):
        # No cloud configured, should return local mock even for complex_reasoning
        result = await router.route_and_generate(
            prompt="Complex reasoning task",
            task_type="complex_reasoning",
        )
        assert result["routed_to"] == "local_ollama"

    @pytest.mark.asyncio
    async def test_model_selection_fast_for_classification(self, router):
        result = await router.route_and_generate(
            prompt="Test",
            task_type="classification",
        )
        assert result["model"] == "qwen2.5:1.5b"

    @pytest.mark.asyncio
    async def test_model_selection_reasoning_for_complex(self, router):
        result = await router.route_and_generate(
            prompt="Test",
            task_type="complex_reasoning",
        )
        assert result["model"] == "llama3.1:8b"


class TestHybridRouterWithMockedHTTP:
    """Test HybridRouter with mocked httpx responses."""

    @pytest.fixture
    def router(self):
        return HybridRouter(
            ollama_url="http://test-ollama:11434/api/chat",
            local_openai_url="http://test-vllm:8000",
            cloud_api_url="https://api.cloud.test",
            cloud_api_key="test-key",
            mock_mode=False,
            confidence_threshold=0.95,  # Force cloud escalation
        )

    @pytest.mark.asyncio
    async def test_ollama_json_mode_success(self, router):
        mock_response = {
            "message": {"content": '{"classification": "spam", "confidence": 0.95}'},
            "model": "qwen2.5:1.5b",
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )

            result = await router.route_and_generate(
                prompt="Classify this email",
                task_type="classification",
                response_format={"type": "json_object"},
            )

            assert result["routed_to"] == "local_ollama"
            assert "classification" in result["text"]
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ollama_fallback_to_openai_compatible(self, router):
        ollama_error = httpx.ConnectError("Connection refused")
        openai_response = {
            "choices": [{"message": {"content": '{"result": "success"}'}}],
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [ollama_error, MagicMock(
                status_code=200,
                json=lambda: openai_response,
                raise_for_status=lambda: None,
            )]

            result = await router.route_and_generate(
                prompt="Test prompt",
                task_type="classification",
            )

            assert result["routed_to"] == "local_openai_compatible"
            assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_pii_scrubbing_on_cloud_escalation(self, router):
        local_response = {
            "message": {"content": "Low confidence result"},
            "model": "llama3.1:8b",
        }
        # Cloud returns response with the ORIGINAL PII (since it received scrubbed version)
        # The local restore should put it back
        cloud_response = {
            "choices": [{"message": {"content": "Cloud result for [REDACTED_EMAIL_1]"}}],
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [
                MagicMock(status_code=200, json=lambda: local_response, raise_for_status=lambda: None),
                MagicMock(status_code=200, json=lambda: cloud_response, raise_for_status=lambda: None),
            ]

            prompt = "Email john@secret.com for details"
            result = await router.route_and_generate(
                prompt=prompt,
                task_type="complex_reasoning",
            )

            assert result["routed_to"] == "frontier_cloud"
            # The restored text should have the original email
            assert "john@secret.com" in result["text"]
            assert "[REDACTED_EMAIL_1]" not in result["text"]
            assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_cloud_failure_fallback(self, router):
        local_response = {
            "message": {"content": "Local result"},
            "model": "llama3.1:8b",
        }

        # Mock both Ollama and Cloud clients
        async def mock_ollama_chat(*args, **kwargs):
            return local_response

        mock_cloud = MagicMock()
        mock_cloud.chat = AsyncMock(side_effect=httpx.ConnectError("Cloud unavailable"))
        router._cloud = mock_cloud

        with patch.object(router.ollama, 'chat', side_effect=mock_ollama_chat):
            result = await router.route_and_generate(
                prompt="Complex task",
                task_type="complex_reasoning",
            )

            # Should fall back to mock with mock_fallback suffix
            assert result["routed_to"] == "frontier_cloud_mock_fallback"


class TestContextGraphDecay:
    """Test ContextKnowledgeGraph temporal decay and consolidation."""

    @pytest.fixture
    def graph(self):
        return ContextKnowledgeGraph()

    def test_compute_decayed_relevance_fresh_node(self, graph):
        node = ContextNode(
            node_id="test-1",
            node_type=ContextNodeType.FACT,
            title="Fresh Fact",
            content={"data": "test"},
            confidence=1.0,
            decay_rate=0.05,
            access_count=0,
            last_accessed=datetime.now(UTC),
            created_at=datetime.now(UTC),
            source="test",
            tags=["test"],
        )
        relevance = node.compute_decayed_relevance()
        assert relevance == 1.0  # Fresh node with confidence 1.0

    def test_compute_decayed_relevance_aged_node(self, graph):
        old_time = datetime.now(UTC) - timedelta(days=10)
        node = ContextNode(
            node_id="test-2",
            node_type=ContextNodeType.FACT,
            title="Old Fact",
            content={"data": "test"},
            confidence=1.0,
            decay_rate=0.05,
            access_count=0,
            last_accessed=old_time,
            created_at=old_time,
            source="test",
            tags=["test"],
        )
        relevance = node.compute_decayed_relevance()
        # (1 - 0.05)^10 = 0.95^10 ≈ 0.599
        assert 0.55 < relevance < 0.65

    def test_compute_decayed_relevance_with_access_boost(self, graph):
        old_time = datetime.now(UTC) - timedelta(days=10)
        node = ContextNode(
            node_id="test-3",
            node_type=ContextNodeType.FACT,
            title="Accessed Fact",
            content={"data": "test"},
            confidence=1.0,
            decay_rate=0.05,
            access_count=5,  # 5 accesses
            last_accessed=old_time,
            created_at=old_time,
            source="test",
            tags=["test"],
        )
        relevance = node.compute_decayed_relevance()
        # Base decay * (1 + 0.1 * 5) = 0.599 * 1.5 = 0.898
        assert 0.85 < relevance < 0.95

    def test_find_nodes_sorts_by_relevance(self, graph):
        now = datetime.now(UTC)
        old_time = now - timedelta(days=20)

        fresh_node = ContextNode(
            node_id="fresh",
            node_type=ContextNodeType.FACT,
            title="Fresh",
            content={},
            confidence=1.0,
            decay_rate=0.05,
            last_accessed=now,
            created_at=now,
            source="test",
            tags=["test"],
        )
        old_node = ContextNode(
            node_id="old",
            node_type=ContextNodeType.FACT,
            title="Old",
            content={},
            confidence=1.0,
            decay_rate=0.05,
            last_accessed=old_time,
            created_at=old_time,
            source="test",
            tags=["test"],
        )

        graph.upsert_node(fresh_node)
        graph.upsert_node(old_node)

        results = graph.find_nodes(node_type=ContextNodeType.FACT, tag="test")
        assert results[0].node_id == "fresh"
        assert results[1].node_id == "old"


class TestEditDeltaEngine:
    """Test EditDeltaEngine diff rule induction edge cases."""

    @pytest.fixture
    def engine(self):
        return EditDeltaEngine()

    @pytest.mark.asyncio
    async def test_no_text_delta_returns_none(self, engine):
        envelope = ActionEnvelope(
            action_id="test-1",
            agent_id="test_agent",
            session_id="sess-1",
            tool_name="gmail_send_message",
            tier=TierLevel.TIER_2_COMMIT,
            risk_score=0.8,
            risk_factors=["external"],
            payload={"to": "test@test.com", "subject": "Test", "body": "Hello"},
        )
        revised = {"to": "test@test.com", "subject": "Test", "body": "Hello"}

        result = await engine.learn_from_edit(envelope, revised)
        assert result is None

    @pytest.mark.asyncio
    async def test_brevity_rule_induction(self, engine):
        envelope = ActionEnvelope(
            action_id="test-2",
            agent_id="test_agent",
            session_id="sess-1",
            tool_name="gmail_send_message",
            tier=TierLevel.TIER_2_COMMIT,
            risk_score=0.8,
            risk_factors=["external"],
            payload={
                "to": "test@test.com",
                "subject": "Test",
                "body": "Dear Partner,\n\nI hope this email finds you well. We would love to collaborate on this project. Please let me know your thoughts.\n\nBest regards,\nTeam",
            },
        )
        revised = {
            "to": "test@test.com",
            "subject": "Test",
            "body": "Hi Partner,\n\nConfirming collaboration.\n\nThanks,\nTeam",
        }

        result = await engine.learn_from_edit(envelope, revised)
        assert result is not None
        assert "brevity" in result.action_directive.lower() or "concise" in result.action_directive.lower()

    @pytest.mark.asyncio
    async def test_formality_rule_induction(self, engine):
        envelope = ActionEnvelope(
            action_id="test-3",
            agent_id="test_agent",
            session_id="sess-1",
            tool_name="gmail_send_message",
            tier=TierLevel.TIER_2_COMMIT,
            risk_score=0.8,
            risk_factors=["external"],
            payload={
                "to": "test@test.com",
                "subject": "Test",
                "body": "Dear Sir,\n\nI hope this email finds you well. Please find the attached document.\n\nBest regards,\nJohn",
            },
        )
        revised = {
            "to": "test@test.com",
            "subject": "Test",
            "body": "Here's the document you requested.",
        }

        result = await engine.learn_from_edit(envelope, revised)
        assert result is not None
        assert "pleasantr" in result.inferred_delta_rationale.lower() or "formal" in result.inferred_delta_rationale.lower()

    @pytest.mark.asyncio
    async def test_bullet_formatting_rule_induction(self, engine):
        envelope = ActionEnvelope(
            action_id="test-4",
            agent_id="test_agent",
            session_id="sess-1",
            tool_name="gmail_send_message",
            tier=TierLevel.TIER_2_COMMIT,
            risk_score=0.8,
            risk_factors=["external"],
            payload={
                "to": "test@test.com",
                "subject": "Test",
                "body": "We need to do three things: first, review the code. Second, write tests. Third, deploy.",
            },
        )
        revised = {
            "to": "test@test.com",
            "subject": "Test",
            "body": "Action items:\n- Review code\n- Write tests\n- Deploy",
        }

        result = await engine.learn_from_edit(envelope, revised)
        assert result is not None
        assert "bullet" in result.action_directive.lower() or "list" in result.action_directive.lower()

    @pytest.mark.asyncio
    async def test_rule_reinforcement(self, engine):
        envelope = ActionEnvelope(
            action_id="test-5",
            agent_id="test_agent",
            session_id="sess-1",
            tool_name="gmail_send_message",
            tier=TierLevel.TIER_2_COMMIT,
            risk_score=0.8,
            risk_factors=["external"],
            payload={"to": "a@b.com", "subject": "Test", "body": "Long verbose message with many words here"},
        )
        revised = {"to": "a@b.com", "subject": "Test", "body": "Short."}

        # First edit
        rule1 = await engine.learn_from_edit(envelope, revised)
        assert rule1 is not None
        initial_weight = rule1.weight

        # Second similar edit (same trigger pattern)
        envelope2 = ActionEnvelope(
            action_id="test-6",
            agent_id="test_agent",
            session_id="sess-2",
            tool_name="gmail_send_message",
            tier=TierLevel.TIER_2_COMMIT,
            risk_score=0.8,
            risk_factors=["external"],
            payload={"to": "a@b.com", "subject": "Test2", "body": "Another long verbose message with many words here"},
        )
        revised2 = {"to": "a@b.com", "subject": "Test2", "body": "Brief."}

        rule2 = await engine.learn_from_edit(envelope2, revised2)
        assert rule2 is not None
        assert rule2.rule_id == rule1.rule_id  # Same rule reinforced
        assert rule2.weight > initial_weight
        assert rule2.reinforcement_count == 2


class TestFrameDeduplicator:
    """Test FrameDeduplicator LRU cache behavior."""

    def test_new_frame_accepted(self):
        frame_deduplicator.clear()
        assert frame_deduplicator.check_and_add("frame-1") is True

    def test_duplicate_frame_rejected(self):
        frame_deduplicator.clear()
        frame_deduplicator.check_and_add("frame-2")
        assert frame_deduplicator.check_and_add("frame-2") is False

    def test_lru_eviction(self):
        frame_deduplicator.clear()
        # Fill cache to max (10000)
        for i in range(10000):
            frame_deduplicator.check_and_add(f"frame-{i}")

        # Add one more, should evict oldest
        frame_deduplicator.check_and_add("frame-new")

        # Oldest should be evicted
        assert frame_deduplicator.check_and_add("frame-0") is True  # Treated as new
        assert frame_deduplicator.check_and_add("frame-new") is False  # Still in cache


class TestHybridRouterShutdown:
    """Test router lifecycle."""

    @pytest.mark.asyncio
    async def test_get_and_shutdown_router(self):
        router = get_hybrid_router()
        assert router is not None
        await shutdown_hybrid_router()

        # Getting again should create new instance
        router2 = get_hybrid_router()
        assert router2 is not None
        await shutdown_hybrid_router()
