"""
Integration tests for Chrome Extension WebSocket Bridge & Workflow Frames.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

import json
from datetime import UTC, datetime

import pytest
from app.config import get_or_create_bridge_token, settings
from app.main import app, context_graph, policy_enclave, tool_dispatcher
from app.schemas import BridgeMessage, BridgeMessageType, ContextNodeType
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def test_ws_browser_bridge_unauthorized():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/browser-bridge?token=invalid_token"):
            pass
    assert excinfo.value.code == 1008


def test_ws_browser_bridge_telemetry_and_quarantine():
    client = TestClient(app)
    token = get_or_create_bridge_token()

    with client.websocket_connect(f"/ws/browser-bridge?token={token}") as ws:
        # 1. Send Grocery Price Snapshot frame
        grocery_payload = {
            "portal": "instacart.com",
            "title": "Organic Almond Milk 64 fl oz",
            "unit_price": 3.99,
            "currency": "USD",
            "in_stock": True,
            "url": "https://www.instacart.com/products/123",
            "can_stage_cart": True,
            "extracted_at": datetime.now(UTC).isoformat(),
        }
        msg_grocery = BridgeMessage(
            msg_type=BridgeMessageType.DOM_GROCERY_SNAPSHOT,
            payload=grocery_payload,
        )
        ws.send_text(msg_grocery.model_dump_json())

        # 2. Send WhatsApp incoming message frame
        wa_payload = {
            "app": "whatsapp_web",
            "chat_name": "Research Group",
            "sender": "Alex",
            "text": "Please confirm the server IP when you can.",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        msg_wa = BridgeMessage(
            msg_type=BridgeMessageType.WHATSAPP_INCOMING_MESSAGE,
            payload=wa_payload,
        )
        ws.send_text(msg_wa.model_dump_json())

        # 3. Send Instagram saved posts batch frame
        ig_payload = {
            "items": [
                {
                    "post_url": "https://instagram.com/p/DB12345",
                    "author": "ai_researcher",
                    "caption": "New paper on temporal graph pruning for autonomous agents.",
                    "extracted_at": datetime.now(UTC).isoformat(),
                }
            ]
        }
        msg_ig = BridgeMessage(
            msg_type=BridgeMessageType.INSTAGRAM_SAVED_BATCH,
            payload=ig_payload,
        )
        ws.send_text(msg_ig.model_dump_json())

    # Verify context_graph stored nodes with prompt injection quarantine
    grocery_nodes = context_graph.find_nodes(node_type=ContextNodeType.FACT, tag="instacart.com")
    assert len(grocery_nodes) > 0
    assert "<untrusted_dom_payload>" in grocery_nodes[0].content["sanitized_title"]

    wa_nodes = context_graph.find_nodes(node_type=ContextNodeType.EPISODE, tag="whatsapp")
    assert len(wa_nodes) > 0
    assert "<untrusted_dom_payload>" in wa_nodes[0].content["sanitized_text"]

    ig_nodes = context_graph.find_nodes(node_type=ContextNodeType.FACT, tag="instagram")
    assert len(ig_nodes) > 0
    assert "<untrusted_dom_payload>" in ig_nodes[0].content["sanitized_caption"]


@pytest.mark.asyncio
async def test_commit_action_dispatched_to_connected_browser():
    """
    Verifies that when a Chrome Extension client is connected, ToolDispatcher.dispatch()
    formats and transmits a COMMIT_ACTION frame over WebSocket.
    """
    client = TestClient(app)
    token = get_or_create_bridge_token()

    with client.websocket_connect(f"/ws/browser-bridge?token={token}") as ws:
        # Dispatch WhatsApp send while browser bridge is connected
        send_payload = {
            "chat_name": "Team Lead",
            "text": "Staged report is approved and ready.",
        }
        # Prepare and claim an envelope to satisfy zero-trust policy
        envelope = policy_enclave.prepare_envelope(
            tool_name="whatsapp_send_message",
            payload=send_payload,
            agent_id="test_agent",
            session_id="test_sess",
        )
        policy_enclave.queue.enqueue(envelope)
        policy_enclave.queue.claim_for_execution(envelope.action_id)

        # Mint capability token for authorized execution
        from capabilities import mint_capability_token
        capability_token = mint_capability_token(
            action_id=envelope.action_id,
            tool_name="whatsapp_send_message",
            payload=send_payload,
            profile=settings.PROFILE,
        )

        dispatch_result = await tool_dispatcher.dispatch(
            "whatsapp_send_message",
            send_payload,
            envelope_id=envelope.action_id,
            capability_token=capability_token,
        )
        assert dispatch_result["status"] == "DISPATCHED_TO_BROWSER"

        # The connected extension receives the COMMIT_ACTION frame over WS
        raw_frame = ws.receive_text()
        frame_data = json.loads(raw_frame)
        assert frame_data["msg_type"] == "COMMIT_ACTION"
        assert frame_data["payload"]["command"] == "COMMIT_WHATSAPP_SEND"
        assert frame_data["payload"]["text"] == "Staged report is approved and ready."
