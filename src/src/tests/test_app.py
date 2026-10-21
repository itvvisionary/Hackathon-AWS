"""Offline tests for the FastAPI serving layer and the Lambda dispatcher.

Everything external is mocked: ``agent.run_agent``, the ``data.*`` DynamoDB
helpers and the ``whatsapp.*`` send/extract helpers (all patched on the ``app``
module where they are referenced). The HTTP routes are exercised with
``TestClient`` and the dispatcher is checked with synthetic events, so the whole
file runs fully offline with no AWS or network.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app


@pytest.fixture
def mocked(monkeypatch):
    """Patch app.agent/app.data/app.whatsapp and return the mocks."""
    run_agent = MagicMock(return_value="agent reply")
    monkeypatch.setattr(app.agent, "run_agent", run_agent)

    append_turn = MagicMock()
    get_mapping = MagicMock(return_value=None)
    get_history = MagicMock(return_value=[])
    monkeypatch.setattr(app.data, "append_turn", append_turn)
    monkeypatch.setattr(app.data, "get_mapping", get_mapping)
    monkeypatch.setattr(app.data, "get_history", get_history)

    extract_webhook_data = MagicMock()
    extract_text_messages = MagicMock(return_value=[])
    send_whatsapp_message = MagicMock()
    monkeypatch.setattr(app.whatsapp, "extract_webhook_data", extract_webhook_data)
    monkeypatch.setattr(app.whatsapp, "extract_text_messages", extract_text_messages)
    monkeypatch.setattr(app.whatsapp, "send_whatsapp_message", send_whatsapp_message)

    # The callback path only sends WhatsApp when this env var is present; keep it
    # absent by default so tests prove the "no WhatsApp config" path is safe.
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)

    class Harness:
        pass

    harness = Harness()
    harness.run_agent = run_agent
    harness.append_turn = append_turn
    harness.get_mapping = get_mapping
    harness.get_history = get_history
    harness.extract_webhook_data = extract_webhook_data
    harness.extract_text_messages = extract_text_messages
    harness.send_whatsapp_message = send_whatsapp_message
    return harness


@pytest.fixture
def client():
    return TestClient(app.app)


# --- GET / -----------------------------------------------------------------


def test_index_returns_html(mocked, client):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "<html" in res.text.lower()


# --- POST /ask -------------------------------------------------------------


def test_ask_returns_reply_and_appends_two_turns(mocked, client):
    res = client.post(
        "/ask", json={"conversation_id": "conv-1", "message": "hello"}
    )
    assert res.status_code == 200
    assert res.json() == {"reply": "agent reply", "conversation_id": "conv-1"}

    mocked.run_agent.assert_called_once_with("conv-1", "hello")
    assert mocked.append_turn.call_count == 2
    mocked.append_turn.assert_any_call("conv-1", "user", "hello")
    mocked.append_turn.assert_any_call("conv-1", "assistant", "agent reply")


# --- POST /callback --------------------------------------------------------


def test_callback_api_channel_stores_without_whatsapp(mocked, client, monkeypatch):
    # API channel: even if WhatsApp were configured, nothing is sent; the
    # message is only stored to history for the UI to load.
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pn-123")
    mocked.get_mapping.return_value = {
        "conversation_id": "conv-2",
        "workflow_id": "wf-2",
        "channel": "api",
    }
    res = client.post(
        "/callback",
        json={
            "conversation_id": "conv-2",
            "workflow_id": "wf-2",
            "receipt_id": "RCPT-9",
            "total_cost": "42.50",
        },
    )
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "channel": "api"}

    mocked.get_mapping.assert_called_once_with("conv-2")
    expected = "Your order is complete. Receipt RCPT-9. Total cost 42.50."
    mocked.append_turn.assert_called_once_with("conv-2", "assistant", expected)
    # API channel -> never send WhatsApp even though it's configured.
    mocked.send_whatsapp_message.assert_not_called()


def test_callback_rejects_unknown_conversation(mocked, client):
    mocked.get_mapping.return_value = None  # no mapping for this conversation
    res = client.post(
        "/callback",
        json={
            "conversation_id": "nope",
            "workflow_id": "wf-x",
            "receipt_id": "RCPT",
            "total_cost": 1,
        },
    )
    assert res.status_code == 404
    mocked.append_turn.assert_not_called()
    mocked.send_whatsapp_message.assert_not_called()


def test_callback_rejects_workflow_id_mismatch(mocked, client):
    mocked.get_mapping.return_value = {
        "conversation_id": "conv-m",
        "workflow_id": "wf-REAL",
    }
    res = client.post(
        "/callback",
        json={
            "conversation_id": "conv-m",
            "workflow_id": "wf-STALE",  # does not match stored wf-REAL
            "receipt_id": "RCPT",
            "total_cost": 1,
        },
    )
    assert res.status_code == 404
    mocked.append_turn.assert_not_called()
    mocked.send_whatsapp_message.assert_not_called()


def test_callback_sends_whatsapp_when_configured(mocked, client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pn-123")
    mocked.get_mapping.return_value = {
        "workflow_id": "wf-3",
        "channel": "whatsapp",
        "whatsapp_number": "+15551230000",
    }

    res = client.post(
        "/callback",
        json={
            "conversation_id": "conv-3",
            "workflow_id": "wf-3",
            "receipt_id": "RCPT-3",
            "total_cost": 10,
        },
    )
    assert res.status_code == 200
    expected = "Your order is complete. Receipt RCPT-3. Total cost 10."
    mocked.send_whatsapp_message.assert_called_once_with("+15551230000", expected)
    mocked.append_turn.assert_called_once_with("conv-3", "assistant", expected)


def test_callback_does_not_crash_when_whatsapp_send_fails(mocked, client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pn-123")
    mocked.get_mapping.return_value = {
        "workflow_id": "wf-4",
        "channel": "whatsapp",
        "whatsapp_number": "+15551230000",
    }
    mocked.send_whatsapp_message.side_effect = RuntimeError("boom")

    res = client.post(
        "/callback",
        json={
            "conversation_id": "conv-4",
            "workflow_id": "wf-4",
            "receipt_id": "RCPT-4",
            "total_cost": 1,
        },
    )
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "channel": "whatsapp"}
    # The assistant turn is still recorded despite the WhatsApp failure.
    mocked.append_turn.assert_called_once()


def test_history_returns_turns(mocked, client):
    mocked.get_history.return_value = [
        {"role": "user", "text": "hi", "created_at": 1},
        {"role": "assistant", "text": "hello", "created_at": 2},
    ]
    res = client.get("/history", params={"conversation_id": "conv-h"})
    assert res.status_code == 200
    body = res.json()
    assert body["conversation_id"] == "conv-h"
    assert body["turns"] == [
        {"role": "user", "text": "hi", "created_at": 1},
        {"role": "assistant", "text": "hello", "created_at": 2},
    ]
    mocked.get_history.assert_called_once_with("conv-h")


# --- Dispatcher routing ----------------------------------------------------


def test_lambda_handler_routes_sqs_event_to_batch_path(mocked, monkeypatch):
    batch = MagicMock(return_value={"batchItemFailures": []})
    monkeypatch.setattr(app, "process_sqs_batch", batch)
    handler = MagicMock()
    monkeypatch.setattr(app, "handler", handler)

    event = {"Records": [{"eventSource": "aws:sqs", "messageId": "m-1", "body": "{}"}]}
    result = app.lambda_handler(event, None)

    batch.assert_called_once_with(event)
    assert result == {"batchItemFailures": []}
    handler.assert_not_called()


def test_lambda_handler_routes_http_event_to_mangum(mocked, monkeypatch):
    batch = MagicMock()
    monkeypatch.setattr(app, "process_sqs_batch", batch)
    handler = MagicMock(return_value={"statusCode": 200})
    monkeypatch.setattr(app, "handler", handler)

    event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/"}
    result = app.lambda_handler(event, "ctx")

    handler.assert_called_once_with(event, "ctx")
    assert result == {"statusCode": 200}
    batch.assert_not_called()


def test_process_sqs_batch_isolates_per_record_failures(mocked):
    # First record parses to one message; second record raises during extract.
    mocked.extract_text_messages.side_effect = [
        [{"sender": "+15551230000", "text": "hi", "message_id": "wamid-1"}],
    ]
    mocked.extract_webhook_data.side_effect = [{"ok": True}, RuntimeError("bad")]

    event = {
        "Records": [
            {"eventSource": "aws:sqs", "messageId": "m-ok", "body": "{}"},
            {"eventSource": "aws:sqs", "messageId": "m-bad", "body": "{}"},
        ]
    }
    result = app.process_sqs_batch(event)

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-bad"}]}
    # The good record drove a full agent turn + outbound WhatsApp reply.
    mocked.run_agent.assert_called_once_with(
        "+15551230000", "hi", whatsapp_number="+15551230000"
    )
    mocked.send_whatsapp_message.assert_called_once_with("+15551230000", "agent reply")
