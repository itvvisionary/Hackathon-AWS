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


def test_callback_api_channel_composes_order_list(mocked, client, monkeypatch):
    # API channel: even if WhatsApp were configured, nothing is sent; the
    # composed order-list message is only stored to history for the UI to load.
    # The workflow_id in the message comes from the stored mapping.
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pn-123")
    mocked.get_mapping.return_value = {
        "conversation_id": "conv-2",
        "workflow_id": "237436573",
        "channel": "api",
    }
    # Real RPA shape: each field is ONE semicolon-separated string, and
    # Cust_Name is a single party name that applies to every row. The RPA does
    # NOT send customer_found, so it is omitted here on purpose — orders must
    # still show (presence is derived from the data, not the flag).
    res = client.post(
        "/callback",
        json={
            "conversation_id": "conv-2",
            "Sales_docs": "21000232 ; 21000228",
            "Net_Prices": "43.409,65 ; 237.420,00",
            "Currencies": "EGP ; EGP",
            "Cust_Name": "Dentistry hospital",
            "Doc_dates": "16.03.2026 ; 03.03.2026",
        },
    )
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "channel": "api"}

    mocked.get_mapping.assert_called_once_with("conv-2")
    expected = (
        "Orders — Workflow 237436573  Customer: Dentistry Hospital\n"
        "\n"
        "1. Doc 21000232 — 43,409.65 EGP — 16 Mar 2026\n"
        "2. Doc 21000228 — 237,420.00 EGP — 03 Mar 2026\n"
        "\n"
        "Total: 280,829.65 EGP (2 orders)"
    )
    mocked.append_turn.assert_called_once_with("conv-2", "assistant", expected)
    # API channel -> never send WhatsApp even though it's configured.
    mocked.send_whatsapp_message.assert_not_called()


def test_callback_rejects_unknown_conversation(mocked, client):
    mocked.get_mapping.return_value = None  # no mapping for this conversation
    res = client.post(
        "/callback",
        json={
            "conversation_id": "nope",
            "customer_found": True,
            "Sales_docs": ["4500001"],
        },
    )
    assert res.status_code == 404
    mocked.append_turn.assert_not_called()
    mocked.send_whatsapp_message.assert_not_called()


def test_callback_sends_whatsapp_when_configured(mocked, client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pn-123")
    mocked.get_mapping.return_value = {
        "workflow_id": "237436573",
        "channel": "whatsapp",
        "whatsapp_number": "+15551230000",
    }

    res = client.post(
        "/callback",
        json={
            "conversation_id": "conv-3",
            "customer_found": True,
            "Sales_docs": "4500003",
            "Net_Prices": "10",
            "Currencies": "EUR",
            "Cust_Name": "Globex",
            "Doc_dates": "2026-03-03",
        },
    )
    assert res.status_code == 200
    # Price "10" parses to 10.00; date "2026-03-03" isn't DD.MM.YYYY so it is
    # shown unchanged (graceful fallback).
    expected = (
        "Orders — Workflow 237436573  Customer: Globex\n"
        "\n"
        "1. Doc 4500003 — 10.00 EUR — 2026-03-03\n"
        "\n"
        "Total: 10.00 EUR (1 orders)"
    )
    mocked.send_whatsapp_message.assert_called_once_with("+15551230000", expected)
    mocked.append_turn.assert_called_once_with("conv-3", "assistant", expected)


def test_callback_does_not_crash_when_whatsapp_send_fails(mocked, client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "pn-123")
    mocked.get_mapping.return_value = {
        "workflow_id": "237436573",
        "channel": "whatsapp",
        "whatsapp_number": "+15551230000",
    }
    mocked.send_whatsapp_message.side_effect = RuntimeError("boom")

    res = client.post(
        "/callback",
        json={
            "conversation_id": "conv-4",
            "customer_found": True,
            "Sales_docs": ["4500004"],
        },
    )
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "channel": "whatsapp"}
    # The assistant turn is still recorded despite the WhatsApp failure.
    mocked.append_turn.assert_called_once()


def test_callback_customer_not_found_message(mocked, client):
    mocked.get_mapping.return_value = {
        "workflow_id": "237436573",
        "channel": "api",
    }
    # No order data at all -> "no orders" (derived from empty data, not a flag).
    res = client.post(
        "/callback",
        json={"conversation_id": "conv-nf"},
    )
    assert res.status_code == 200
    expected = "No orders found for the requested customer (workflow 237436573)."
    mocked.append_turn.assert_called_once_with("conv-nf", "assistant", expected)
    mocked.send_whatsapp_message.assert_not_called()


def test_callback_missing_optional_fields_does_not_422(mocked, client):
    # All per-order fields omitted -> must NOT 422 (lenient). With no data the
    # "no orders" branch is composed and the route runs normally.
    mocked.get_mapping.return_value = {
        "workflow_id": "237436573",
        "channel": "api",
    }
    res = client.post("/callback", json={"conversation_id": "c"})
    assert res.status_code == 200
    expected = "No orders found for the requested customer (workflow 237436573)."
    mocked.get_mapping.assert_called_once_with("c")
    mocked.append_turn.assert_called_once_with("c", "assistant", expected)


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
