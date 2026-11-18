"""
End-to-End Integration Tests for Tier-2 Lifecycle & Idempotency.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

import pytest
import httpx
from datetime import datetime, timezone

from app.schemas import ActionEnvelope, ApprovalStatus, TierLevel, DiffPreview
from app.main import app, policy_enclave, tool_dispatcher, edit_delta_engine
from app.dispatcher import BridgeDisconnectedError
from enclave import ActionSuspendedException, IdempotencyConflictError


@pytest.mark.asyncio
async def test_tier2_full_approval_lifecycle_with_edits():
    """
    Step 1: Agent proposes a TIER_2 action -> Suspended & queued as PENDING.
    Step 2: User resolves via API with EDITED payload -> EditDeltaEngine creates rule -> Dispatcher runs ONCE with edited payload.
    Step 3: Attempting to resolve a second time raises 409 Conflict (Idempotency).
    """
    dispatch_call_count = 0
    dispatched_payload = None

    async def mock_gmail_handler(payload):
        nonlocal dispatch_call_count, dispatched_payload
        dispatch_call_count += 1
        dispatched_payload = payload
        return {"status": "DELIVERED", "id": "msg_999"}

    tool_dispatcher.register_handler("gmail_send_message", mock_gmail_handler)

    # 1. Propose action and enqueue as PENDING
    initial_payload = {
        "to": "partner@research.org",
        "subject": "Proposal",
        "body": "Dear Partner,\n\nI hope this email finds you well. We would love to collaborate.\n\nSincerely,\nTeam",
    }
    envelope = policy_enclave.prepare_envelope(
        tool_name="gmail_send_message",
        payload=initial_payload,
        agent_id="test_agent",
        session_id="sess_101",
        diff_preview=DiffPreview(original="old text", proposed=initial_payload["body"]),
    )
    policy_enclave.queue.enqueue(envelope)

    # Verify initial pending state
    fetched = policy_enclave.queue.get(envelope.action_id)
    assert fetched is not None
    assert fetched.status == ApprovalStatus.PENDING
    assert dispatch_call_count == 0

    # 2. Resolve via HTTP API with EDITED payload
    edited_payload = {
        "to": "partner@research.org",
        "subject": "Proposal",
        "body": "Hi Partner,\n\nConfirming our collaboration start date.\n- Kickoff: Monday",
    }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/approvals/{envelope.action_id}/resolve",
            json={
                "status": "EDITED",
                "user_signature": "sig_sandeep_2026",
                "edited_payload": edited_payload,
                "user_feedback": "Shortened and added bullets.",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "EDITED"
        assert data["execution_result"] == {"status": "DELIVERED", "id": "msg_999"}
        assert data["payload"]["body"] == edited_payload["body"]

    # Verify tool handler was called ONCE with the EDITED payload
    assert dispatch_call_count == 1
    assert dispatched_payload == edited_payload

    # Verify EditDeltaEngine persisted a rule
    rules = edit_delta_engine.get_applicable_rules()
    assert len(rules) > 0

    # 3. Idempotency test: Attempt duplicate resolve call -> MUST return 409 Conflict
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        dup_response = await client.post(
            f"/api/approvals/{envelope.action_id}/resolve",
            json={
                "status": "APPROVED",
                "user_signature": "sig_duplicate_click",
            },
        )
        assert dup_response.status_code == 409
        assert "Duplicate resolution rejected" in dup_response.json()["detail"]

    # Verify tool handler was NEVER called a second time
    assert dispatch_call_count == 1


@pytest.mark.asyncio
async def test_tier2_rejected_lifecycle():
    """
    Verify REJECTED action never invokes the tool dispatcher and transitions cleanly.
    """
    dispatch_call_count = 0

    async def mock_calendar_handler(payload):
        nonlocal dispatch_call_count
        dispatch_call_count += 1
        return {"status": "CONFIRMED"}

    tool_dispatcher.register_handler("calendar_create_event", mock_calendar_handler)

    envelope = policy_enclave.prepare_envelope(
        tool_name="calendar_create_event",
        payload={"title": "Unwanted Meeting"},
        agent_id="test_agent",
        session_id="sess_102",
    )
    policy_enclave.queue.enqueue(envelope)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/approvals/{envelope.action_id}/resolve",
            json={"status": "REJECTED", "user_feedback": "Not needed"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "REJECTED"
        assert data["execution_result"] is None

    # Handler must NOT have been called
    assert dispatch_call_count == 0

    # Idempotency check: Cannot re-resolve a rejected action
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        dup = await client.post(
            f"/api/approvals/{envelope.action_id}/resolve",
            json={"status": "APPROVED"},
        )
        assert dup.status_code == 409


@pytest.mark.asyncio
async def test_browser_bound_handler_disconnected_keeps_action_pending():
    """
    Verify that if a browser-bound action (like whatsapp_send_message) is resolved
    while no Chrome Extension is connected, it raises BridgeDisconnectedError (503)
    and leaves the action in PENDING state for future retry.
    """
    envelope = policy_enclave.prepare_envelope(
        tool_name="whatsapp_send_message",
        payload={"chat_name": "Mom", "text": "Will call in 10 mins"},
        agent_id="whatsapp_agent",
        session_id="sess_103",
    )
    policy_enclave.queue.enqueue(envelope)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/approvals/{envelope.action_id}/resolve",
            json={"status": "APPROVED"},
        )
        assert response.status_code == 503
        assert "Chrome Extension bridge is connected" in response.json()["detail"]

    # Verify action remains PENDING in database
    refetched = policy_enclave.queue.get(envelope.action_id)
    assert refetched.status == ApprovalStatus.PENDING
