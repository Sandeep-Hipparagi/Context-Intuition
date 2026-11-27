"""
Wave 2 Tests: Capability-Based Security, Declarative Policy DSL & Multi-Profile Isolation.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

import time
from pathlib import Path

import httpx
import pytest
from app.config import (
    _validate_profile_name,
    get_db_path,
    get_or_create_bridge_token,
    get_or_create_capability_secret,
    get_vibecore_home,
)
from app.main import app, tool_dispatcher
from app.schemas import TierLevel
from capabilities import (
    CapabilityManager,
    CapabilityVerificationError,
    mint_capability_token,
    verify_capability_token,
)
from enclave import get_policy_enclave
from policy_loader import PolicyLoader, get_policy_loader


class TestProfileValidation:
    """Test profile name validation and isolation."""

    def test_valid_profile_names(self):
        valid = ["default", "personal", "work", "test-profile", "profile_1", "a", "a" * 32]
        for name in valid:
            assert _validate_profile_name(name) == name

    def test_invalid_profile_names(self):
        invalid = ["", "../evil", "..", "profile/", "profile\\", "a" * 33, "profile!", "profile@domain"]
        for name in invalid:
            with pytest.raises(ValueError):
                _validate_profile_name(name)

    def test_get_vibecore_home_profile_isolation(self, tmp_path, monkeypatch):
        """Test that different profiles get isolated directories."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))

        personal_home = get_vibecore_home("personal")
        work_home = get_vibecore_home("work")

        assert personal_home != work_home
        assert "personal" in str(personal_home)
        assert "work" in str(work_home)
        assert personal_home.exists()
        assert work_home.exists()

    def test_bridge_token_per_profile(self, tmp_path, monkeypatch):
        """Test that bridge tokens are isolated per profile."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        # Unset the env token so tokens are generated per profile
        monkeypatch.delenv("VIBECORE_BRIDGE_TOKEN", raising=False)

        token1 = get_or_create_bridge_token("personal")
        token2 = get_or_create_bridge_token("work")

        assert token1 != token2

        # Tokens should be persisted to profile-specific locations
        personal_token_file = test_base / "profiles" / "personal" / "bridge.token"
        work_token_file = test_base / "profiles" / "work" / "bridge.token"

        assert personal_token_file.read_text().strip() == token1
        assert work_token_file.read_text().strip() == token2

    def test_capability_secret_per_profile(self, tmp_path, monkeypatch):
        """Test that capability secrets are isolated per profile."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))

        secret1 = get_or_create_capability_secret("personal")
        secret2 = get_or_create_capability_secret("work")

        assert secret1 != secret2
        assert "personal" in str(secret1)
        assert "work" in str(secret2)

    def test_db_path_per_profile(self, tmp_path, monkeypatch):
        """Test that database paths are isolated per profile."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))

        db1 = get_db_path("test.db", "personal")
        db2 = get_db_path("test.db", "work")

        assert db1 != db2
        assert db1.parent.name == "personal"
        assert db2.parent.name == "work"


class TestCapabilityTokens:
    """Test HMAC-SHA256 capability token minting and verification."""

    @pytest.fixture
    def manager(self, tmp_path, monkeypatch):
        """Create a CapabilityManager with isolated secret."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        return CapabilityManager("test")

    def test_mint_and_verify_valid_token(self, manager):
        """Test minting and verifying a valid token."""
        token = manager.mint("action-123", "gmail_send_message", {"to": "test@test.com", "subject": "Test"})

        # Verify succeeds
        verified = manager.verify(token, "gmail_send_message", {"to": "test@test.com", "subject": "Test"})
        assert verified.action_id == "action-123"
        assert verified.tool_name == "gmail_send_message"
        assert not verified.is_expired()

    def test_verify_rejects_tampered_payload(self, manager):
        """Test that modifying payload after minting fails verification."""
        original_payload = {"to": "test@test.com", "subject": "Test"}
        token = manager.mint("action-123", "gmail_send_message", original_payload)

        # Tamper with payload
        tampered_payload = {"to": "evil@evil.com", "subject": "Hacked"}

        with pytest.raises(CapabilityVerificationError, match="Payload hash mismatch"):
            manager.verify(token, "gmail_send_message", tampered_payload)

    def test_verify_rejects_wrong_tool(self, manager):
        """Test that token for one tool cannot be used for another."""
        token = manager.mint("action-123", "gmail_send_message", {"to": "test@test.com"})

        with pytest.raises(CapabilityVerificationError, match="Token tool mismatch"):
            manager.verify(token, "calendar_create_event", {"to": "test@test.com"})

    def test_verify_rejects_expired_token(self, manager):
        """Test that expired tokens are rejected."""
        token = manager.mint("action-123", "gmail_send_message", {"to": "test@test.com"}, expires_in=0)
        time.sleep(0.1)  # Ensure expiration

        with pytest.raises(CapabilityVerificationError, match="Token expired"):
            manager.verify(token, "gmail_send_message", {"to": "test@test.com"})

    def test_verify_rejects_replay(self, manager):
        """Test that single-use nonce prevents replay attacks."""
        token = manager.mint("action-123", "gmail_send_message", {"to": "test@test.com"})

        # First use succeeds
        manager.verify(token, "gmail_send_message", {"to": "test@test.com"})

        # Second use fails
        with pytest.raises(CapabilityVerificationError, match="already used"):
            manager.verify(token, "gmail_send_message", {"to": "test@test.com"})

    def test_verify_rejects_invalid_signature(self, manager):
        """Test that tokens with invalid signatures are rejected."""
        # Create a token with valid payload but tampered signature
        token = manager.mint("action-123", "gmail_send_message", {"to": "test@test.com"})
        payload_b64, _sig_b64 = token.split(".", 1)
        tampered_token = f"{payload_b64}.invalidsignature"

        with pytest.raises(CapabilityVerificationError, match="Invalid signature"):
            manager.verify(tampered_token, "gmail_send_message", {"to": "test@test.com"})

    def test_global_mint_verify_functions(self, tmp_path, monkeypatch):
        """Test global mint/verify functions with profile isolation."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))

        token = mint_capability_token("action-1", "gmail_send_message", {"to": "a@b.com"}, profile="personal")
        verified = verify_capability_token(token, "gmail_send_message", {"to": "a@b.com"}, profile="personal")
        assert verified.action_id == "action-1"

        # Token from personal profile should not work with work profile
        with pytest.raises(CapabilityVerificationError):
            verify_capability_token(token, "gmail_send_message", {"to": "a@b.com"}, profile="work")


class TestPolicyLoader:
    """Test declarative policy loader with YAML and hot-reload."""

    @pytest.fixture
    def temp_policy(self, tmp_path):
        """Create a temporary policy.yaml for testing."""
        policy_content = """
default_tier: "TIER_2_COMMIT"
hot_reload: true
tools:
  test_tool_tier0:
    tier: "TIER_0_OBSERVE"
    description: "Test TIER_0 tool"
  test_tool_tier1:
    tier: "TIER_1_STAGE"
    description: "Test TIER_1 tool"
  test_tool_tier2:
    tier: "TIER_2_COMMIT"
    description: "Test TIER_2 tool"
    conditions:
      - type: "recipient_domain"
        allowlist:
          - "internal.com"
        allowlist_tier: "TIER_1_STAGE"
      - type: "financial_threshold"
        field: "amount"
        threshold: 100.0
        above_threshold_tier: "TIER_2_COMMIT"
        risk_boost: 0.3
  filesystem_delete:
    tier: "TIER_2_COMMIT"
    description: "Delete file"
    conditions:
      - type: "allowed_roots"
        paths:
          - "staged_clips"
          - "exports"
        deny_traversal: true
domain_allowlists:
  internal:
    - "internal.com"
financial_thresholds:
  low: 10.0
  high: 100.0
sandbox_roots:
  - "staged_clips"
  - "exports"
  - "temp"
"""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(policy_content)
        return str(policy_file)

    def test_load_policy_from_yaml(self, temp_policy):
        """Test loading policy from YAML file."""
        loader = PolicyLoader(temp_policy)
        policy = loader.load()

        assert policy.default_tier == TierLevel.TIER_2_COMMIT
        assert policy.hot_reload is True
        assert "test_tool_tier0" in policy.tools
        assert policy.tools["test_tool_tier0"].tier == TierLevel.TIER_0_OBSERVE
        assert policy.tools["test_tool_tier2"].tier == TierLevel.TIER_2_COMMIT

    def test_get_tool_tier(self, temp_policy):
        """Test getting tool tier with fallback."""
        loader = PolicyLoader(temp_policy)

        assert loader.get_tool_tier("test_tool_tier0") == TierLevel.TIER_0_OBSERVE
        assert loader.get_tool_tier("test_tool_tier1") == TierLevel.TIER_1_STAGE
        assert loader.get_tool_tier("test_tool_tier2") == TierLevel.TIER_2_COMMIT
        # Unknown tool falls back to default
        assert loader.get_tool_tier("unknown_tool") == TierLevel.TIER_2_COMMIT

    def test_recipient_domain_condition_allowlist(self, temp_policy):
        """Test recipient domain allowlist conditional tier override."""
        loader = PolicyLoader(temp_policy)

        # Internal domain -> downgraded to TIER_1_STAGE
        tier, _risk, factors = loader.evaluate("test_tool_tier2", {"recipient": "user@internal.com"}, 0.7)
        assert tier == TierLevel.TIER_1_STAGE
        assert any("allowlist" in f for f in factors)

        # External domain -> stays TIER_2_COMMIT
        tier, _risk, factors = loader.evaluate("test_tool_tier2", {"recipient": "user@external.com"}, 0.7)
        assert tier == TierLevel.TIER_2_COMMIT
        assert any("External domain" in f for f in factors)

    def test_financial_threshold_condition(self, temp_policy):
        """Test financial threshold conditional risk boost."""
        loader = PolicyLoader(temp_policy)

        # Below threshold
        tier, risk, factors = loader.evaluate("test_tool_tier2", {"amount": 50.0}, 0.7)
        assert risk == 0.7
        assert tier == TierLevel.TIER_2_COMMIT

        # Above threshold -> risk boost +0.3
        tier, risk, factors = loader.evaluate("test_tool_tier2", {"amount": 150.0}, 0.7)
        assert risk == 1.0  # capped at 1.0
        assert any("Financial threshold exceeded" in f for f in factors)

    def test_hot_reload_on_modification(self, temp_policy):
        """Test hot-reload when policy file is modified."""
        loader = PolicyLoader(temp_policy)
        policy1 = loader.load()
        assert policy1.default_tier == TierLevel.TIER_2_COMMIT

        # Modify the policy file
        import time
        time.sleep(0.1)  # Ensure mtime changes
        modified_content = """
default_tier: "TIER_1_STAGE"
hot_reload: true
tools:
  new_tool:
    tier: "TIER_0_OBSERVE"
    description: "New tool added"
"""
        Path(temp_policy).write_text(modified_content)

        policy2 = loader.load()  # Should hot-reload

        assert policy2.default_tier == TierLevel.TIER_1_STAGE
        assert "new_tool" in policy2.tools
        assert policy2.tools["new_tool"].tier == TierLevel.TIER_0_OBSERVE

    def test_force_reload(self, temp_policy):
        """Test force reload bypasses mtime check."""
        loader = PolicyLoader(temp_policy)
        policy1 = loader.load(force=True)
        policy2 = loader.load(force=True)
        # Both should work, force=True ignores mtime
        assert policy1 is not None
        assert policy2 is not None


class TestPolicyEnclaveIntegration:
    """Test PolicyEnclave using declarative policy."""

    @pytest.fixture
    def enclave_with_temp_policy(self, tmp_path, monkeypatch):
        """Create PolicyEnclave with temporary policy."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))

        # Create temp policy
        policy_content = """
default_tier: "TIER_2_COMMIT"
tools:
  internal_email:
    tier: "TIER_2_COMMIT"
    conditions:
      - type: "recipient_domain"
        allowlist: ["internal.com"]
        allowlist_tier: "TIER_1_STAGE"
  expensive_purchase:
    tier: "TIER_2_COMMIT"
    conditions:
      - type: "financial_threshold"
        field: "amount"
        threshold: 100.0
        risk_boost: 0.5
"""
        policy_file = test_base / "policy.yaml"
        policy_file.write_text(policy_content)

        # Create enclave with custom policy loader
        from policy_loader import PolicyLoader
        loader = PolicyLoader(str(policy_file))

        from enclave import PolicyEnclave
        return PolicyEnclave(policy_loader=loader)

    def test_prepare_envelope_uses_declarative_policy(self, enclave_with_temp_policy):
        """Test that prepare_envelope evaluates declarative policy conditions."""
        enclave = enclave_with_temp_policy

        # Internal domain -> TIER_1_STAGE
        envelope = enclave.prepare_envelope(
            tool_name="internal_email",
            payload={"to": "user@internal.com", "subject": "Test"},
            agent_id="test",
            session_id="sess",
        )
        assert envelope.tier == TierLevel.TIER_1_STAGE

        # External domain -> TIER_2_COMMIT
        envelope = enclave.prepare_envelope(
            tool_name="internal_email",
            payload={"to": "user@external.com", "subject": "Test"},
            agent_id="test",
            session_id="sess",
        )
        assert envelope.tier == TierLevel.TIER_2_COMMIT

    def test_financial_threshold_in_envelope(self, enclave_with_temp_policy):
        """Test financial threshold affects risk score in envelope."""
        enclave = enclave_with_temp_policy

        # Below threshold
        envelope = enclave.prepare_envelope(
            tool_name="expensive_purchase",
            payload={"amount": 50.0},
            agent_id="test",
            session_id="sess",
        )
        assert envelope.risk_score < 1.0

        # Above threshold
        envelope = enclave.prepare_envelope(
            tool_name="expensive_purchase",
            payload={"amount": 200.0},
            agent_id="test",
            session_id="sess",
        )
        assert envelope.risk_score == 1.0  # capped


class TestDispatcherCapabilityEnforcement:
    """Test ToolDispatcher enforces capability token verification."""

    @pytest.fixture
    def mock_dispatcher(self, tmp_path, monkeypatch):
        """Create ToolDispatcher with mocked handlers."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        dispatcher = tool_dispatcher
        dispatcher._handlers.clear()

        async def mock_handler(payload):
            return {"status": "OK", "payload": payload}

        dispatcher.register_handler("test_tool", mock_handler)
        return dispatcher

    @pytest.mark.asyncio
    async def test_dispatch_without_capability_token_fails(self, mock_dispatcher, tmp_path, monkeypatch):
        """Test that dispatch without capability token raises CapabilityVerificationError."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        enclave = get_policy_enclave()

        # Create and enqueue an envelope
        envelope = enclave.prepare_envelope(
            tool_name="test_tool",
            payload={"data": "test"},
            agent_id="test_agent",
            session_id="sess_1",
        )
        enclave.queue.enqueue(envelope)
        enclave.queue.claim_for_execution(envelope.action_id)

        # The dispatcher wraps the error in its own CapabilityVerificationError
        from app.dispatcher import CapabilityVerificationError as DispatcherCapError
        with pytest.raises(DispatcherCapError, match="Missing capability token"):
            await mock_dispatcher.dispatch("test_tool", {"data": "test"}, envelope_id=envelope.action_id)

    @pytest.mark.asyncio
    async def test_dispatch_with_valid_capability_token_succeeds(self, mock_dispatcher, tmp_path, monkeypatch):
        """Test that dispatch with valid capability token succeeds."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        enclave = get_policy_enclave()

        # Create and enqueue an envelope
        envelope = enclave.prepare_envelope(
            tool_name="test_tool",
            payload={"data": "test"},
            agent_id="test_agent",
            session_id="sess_1",
        )
        enclave.queue.enqueue(envelope)
        enclave.queue.claim_for_execution(envelope.action_id)

        token = mint_capability_token(
            action_id=envelope.action_id,
            tool_name="test_tool",
            payload={"data": "test"},
            profile="test",
        )

        result = await mock_dispatcher.dispatch(
            "test_tool",
            {"data": "test"},
            envelope_id=envelope.action_id,
            capability_token=token,
        )
        assert result["status"] == "OK"

    @pytest.mark.asyncio
    async def test_dispatch_with_tampered_payload_fails(self, mock_dispatcher, tmp_path, monkeypatch):
        """Test that dispatch with tampered payload fails capability verification."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        enclave = get_policy_enclave()

        # Create and enqueue an envelope
        envelope = enclave.prepare_envelope(
            tool_name="test_tool",
            payload={"data": "test"},
            agent_id="test_agent",
            session_id="sess_1",
        )
        enclave.queue.enqueue(envelope)
        enclave.queue.claim_for_execution(envelope.action_id)

        original_payload = {"data": "test"}
        token = mint_capability_token(
            action_id=envelope.action_id,
            tool_name="test_tool",
            payload=original_payload,
            profile="test",
        )

        # Tamper with payload
        tampered_payload = {"data": "evil"}

        from app.dispatcher import CapabilityVerificationError as DispatcherCapError
        with pytest.raises(DispatcherCapError, match="Payload hash mismatch"):
            await mock_dispatcher.dispatch(
                "test_tool",
                tampered_payload,
                envelope_id=envelope.action_id,
                capability_token=token,
            )

    @pytest.mark.asyncio
    async def test_dispatch_with_expired_token_fails(self, mock_dispatcher, tmp_path, monkeypatch):
        """Test that dispatch with expired token fails."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        enclave = get_policy_enclave()

        # Create and enqueue an envelope
        envelope = enclave.prepare_envelope(
            tool_name="test_tool",
            payload={"data": "test"},
            agent_id="test_agent",
            session_id="sess_1",
        )
        enclave.queue.enqueue(envelope)
        enclave.queue.claim_for_execution(envelope.action_id)

        token = mint_capability_token(
            action_id=envelope.action_id,
            tool_name="test_tool",
            payload={"data": "test"},
            expires_in=0,
            profile="test",
        )
        time.sleep(0.1)

        from app.dispatcher import CapabilityVerificationError as DispatcherCapError
        with pytest.raises(DispatcherCapError, match="Token expired"):
            await mock_dispatcher.dispatch(
                "test_tool",
                {"data": "test"},
                envelope_id=envelope.action_id,
                capability_token=token,
            )

    @pytest.mark.asyncio
    async def test_dispatch_replay_attack_fails(self, mock_dispatcher, tmp_path, monkeypatch):
        """Test that replaying a used token fails."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        enclave = get_policy_enclave()

        # Create and enqueue an envelope
        envelope = enclave.prepare_envelope(
            tool_name="test_tool",
            payload={"data": "test"},
            agent_id="test_agent",
            session_id="sess_1",
        )
        enclave.queue.enqueue(envelope)
        enclave.queue.claim_for_execution(envelope.action_id)

        token = mint_capability_token(
            action_id=envelope.action_id,
            tool_name="test_tool",
            payload={"data": "test"},
            profile="test",
        )

        # First dispatch succeeds
        await mock_dispatcher.dispatch("test_tool", {"data": "test"}, envelope_id=envelope.action_id, capability_token=token)

        # Second dispatch with same token fails
        from app.dispatcher import CapabilityVerificationError as DispatcherCapError
        with pytest.raises(DispatcherCapError, match="already used"):
            await mock_dispatcher.dispatch("test_tool", {"data": "test"}, envelope_id=envelope.action_id, capability_token=token)


class TestApprovalResolutionWithCapability:
    """Test that approval resolution mints and uses capability tokens."""

    @pytest.mark.asyncio
    async def test_resolve_approval_mints_capability_token(self, tmp_path, monkeypatch):
        """Test that resolving an approval mints a capability token and dispatches with it."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        enclave = get_policy_enclave()

        # Register a mock handler
        async def mock_handler(payload):
            return {"status": "SENT"}

        tool_dispatcher._handlers.clear()
        tool_dispatcher.register_handler("gmail_send_message", mock_handler)

        # Create and enqueue an envelope
        envelope = enclave.prepare_envelope(
            tool_name="gmail_send_message",
            payload={"to": "test@test.com", "subject": "Test", "body": "Hello"},
            agent_id="test_agent",
            session_id="sess_1",
        )
        enclave.queue.enqueue(envelope)

        # Resolve via HTTP API
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/approvals/{envelope.action_id}/resolve",
                json={
                    "status": "APPROVED",
                    "user_signature": "sig_dashboard_user",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "APPROVED"
        assert data["execution_result"]["status"] == "SENT"


class TestHealthEndpointProfile:
    """Test that health endpoint includes profile information."""

    @pytest.mark.asyncio
    async def test_health_endpoint_includes_profile(self, tmp_path, monkeypatch):
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "work-profile")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["profile"] == "work-profile"
        assert data["version"] == "0.3.0"


class TestFileDeleteSandbox:
    """Test filesystem delete sandbox enforcement via policy."""

    @pytest.fixture
    def mock_dispatcher_with_file_delete(self, tmp_path, monkeypatch):
        """Create ToolDispatcher with file_delete handler registered."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        dispatcher = tool_dispatcher
        dispatcher._handlers.clear()

        async def mock_file_delete(payload):
            from pathlib import Path

            from app.config import get_vibecore_home

            path_str = payload.get("file_path") or payload.get("path")
            if not path_str:
                raise ValueError("Missing file_path in delete payload")

            target_file = Path(path_str).resolve()
            vibecore_home = get_vibecore_home().resolve()

            # Get sandbox roots from policy
            policy = get_policy_loader().load()
            sandbox_roots = policy.sandbox_roots or ["staged_clips", "exports", "temp"]

            allowed_roots = [vibecore_home / root for root in sandbox_roots]
            allowed_roots.insert(0, vibecore_home)

            is_safe = False
            for root in allowed_roots:
                try:
                    target_file.relative_to(root.resolve())
                    is_safe = True
                    break
                except ValueError:
                    continue

            if not is_safe:
                raise PermissionError(
                    f"Path traversal violation: Target '{target_file}' is outside authorized sandbox directories."
                )

            if target_file.exists() and target_file.is_file():
                target_file.unlink()
                return {"status": "DELETED", "path": str(target_file)}
            return {"status": "SKIPPED_NOT_FOUND", "path": str(target_file)}

        dispatcher.register_handler("file_delete", mock_file_delete)
        return dispatcher

    @pytest.mark.asyncio
    async def test_file_delete_within_sandbox_succeeds(self, tmp_path, monkeypatch, mock_dispatcher_with_file_delete):
        """Test file_delete within allowed sandbox roots succeeds."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        enclave = get_policy_enclave()

        # Create test file in exports
        exports_dir = test_base / "profiles" / "test" / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        test_file = exports_dir / "test.txt"
        test_file.write_text("test content")

        # Create and enqueue envelope
        envelope = enclave.prepare_envelope(
            tool_name="file_delete",
            payload={"file_path": str(test_file)},
            agent_id="test_agent",
            session_id="sess_1",
        )
        enclave.queue.enqueue(envelope)
        enclave.queue.claim_for_execution(envelope.action_id)

        token = mint_capability_token(
            action_id=envelope.action_id,
            tool_name="file_delete",
            payload={"file_path": str(test_file)},
            profile="test",
        )

        result = await mock_dispatcher_with_file_delete.dispatch(
            "file_delete",
            {"file_path": str(test_file)},
            envelope_id=envelope.action_id,
            capability_token=token,
        )
        assert result["status"] == "DELETED"
        assert not test_file.exists()

    @pytest.mark.asyncio
    async def test_file_delete_outside_sandbox_fails(self, tmp_path, monkeypatch, mock_dispatcher_with_file_delete):
        """Test file_delete outside sandbox roots fails."""
        test_base = tmp_path / ".vibecore_test"
        monkeypatch.setenv("VIBECORE_HOME", str(test_base))
        monkeypatch.setenv("VIBECORE_PROFILE", "test")

        enclave = get_policy_enclave()

        # Create test file outside sandbox
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir(parents=True, exist_ok=True)
        test_file = outside_dir / "test.txt"
        test_file.write_text("test content")

        # Create and enqueue envelope
        envelope = enclave.prepare_envelope(
            tool_name="file_delete",
            payload={"file_path": str(test_file)},
            agent_id="test_agent",
            session_id="sess_1",
        )
        enclave.queue.enqueue(envelope)
        enclave.queue.claim_for_execution(envelope.action_id)

        token = mint_capability_token(
            action_id=envelope.action_id,
            tool_name="file_delete",
            payload={"file_path": str(test_file)},
            profile="test",
        )

        with pytest.raises(PermissionError, match="Path traversal violation"):
            await mock_dispatcher_with_file_delete.dispatch(
                "file_delete",
                {"file_path": str(test_file)},
                envelope_id=envelope.action_id,
                capability_token=token,
            )
