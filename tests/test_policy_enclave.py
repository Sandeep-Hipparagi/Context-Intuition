"""
Unit tests for the Zero-Trust Policy Enclave.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

import pytest
from app.schemas import TierLevel, ApprovalStatus
from enclave import PolicyEnclave, ActionSuspendedException, enforce_policy


@pytest.mark.asyncio
async def test_tier_0_observe_executes_immediately(tmp_path):
    db_file = str(tmp_path / "enclave.db")
    enclave = PolicyEnclave(db_path=db_file)

    tier, risk, factors = enclave.evaluate_risk("gmail_read_threads", {})
    assert tier == TierLevel.TIER_0_OBSERVE
    assert risk <= 0.10

    executed = False

    async def mock_tool():
        nonlocal executed
        executed = True
        return {"threads": []}

    envelope = enclave.prepare_envelope("gmail_read_threads", {}, "test_agent", "sess_1")
    result = await enclave.execute_or_suspend(envelope, mock_tool)

    assert executed is True
    assert result == {"threads": []}


@pytest.mark.asyncio
async def test_tier_2_commit_suspends_and_enqueues(tmp_path):
    db_file = str(tmp_path / "enclave.db")
    enclave = PolicyEnclave(db_path=db_file)

    payload = {"to": "investor@external.vc", "amount": 500.0}
    tier, risk, factors = enclave.evaluate_risk("ecommerce_checkout_purchase", payload)
    assert tier == TierLevel.TIER_2_COMMIT
    assert risk >= 0.85
    assert any("Financial threshold exceeded" in f for f in factors)

    envelope = enclave.prepare_envelope("ecommerce_checkout_purchase", payload, "test_agent", "sess_2")

    async def mock_commit():
        return {"status": "PURCHASED"}

    with pytest.raises(ActionSuspendedException):
        await enclave.execute_or_suspend(envelope, mock_commit)

    # Verify action was stored in approval queue
    pending = enclave.queue.list_pending()
    assert len(pending) == 1
    assert pending[0].action_id == envelope.action_id
    assert pending[0].status == ApprovalStatus.PENDING
