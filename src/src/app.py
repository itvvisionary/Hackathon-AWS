"""FastAPI serving layer and the single Lambda dispatcher.

Two triggers share the agent core:

* HTTP (Function URL): FastAPI exposes ``GET /`` (the test page), ``POST /ask``
  (advance the conversation) and ``POST /callback`` (the third-party workflow
  completion hook). ``Mangum`` adapts the ASGI app to the Lambda event shape.
* SQS (WhatsApp): records with ``eventSource == 'aws:sqs'`` are handled by
  ``process_sqs_batch`` which reuses the proven ``whatsapp`` extractors and
  returns the partial-batch-failure shape SQS expects, mirroring handler.py.

``lambda_handler`` is the single entry point that routes between the two. The
HTML page is opened from ``file://`` so CORS is fully permissive. Nothing is
hardcoded; WhatsApp sends are gated on the ``WHATSAPP_PHONE_NUMBER_ID`` env var.
"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from mangum import Mangum
from pydantic import BaseModel

import agent
import data
import whatsapp
from logging_setup import configure_logging, get_logger, log_io

configure_logging()
LOG = get_logger(__name__)

_TEST_PAGE = os.path.join(os.path.dirname(__file__), "test_page.html")

app = FastAPI(title="LangGraph Ordering Agent")

# The test page is loaded from file:// and calls the Function URL directly, so
# the browser needs a fully permissive CORS policy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request bodies --------------------------------------------------------


class AskRequest(BaseModel):
    conversation_id: str
    message: str


class CallbackRequest(BaseModel):
    conversation_id: str
    workflow_id: str
    receipt_id: str
    total_cost: object


# --- HTTP routes -----------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
@log_io()
def index():
    """Serve the vanilla-JS test page that sits next to this module."""
    with open(_TEST_PAGE, "r", encoding="utf-8") as handle:
        return HTMLResponse(content=handle.read())


@app.post("/ask")
@log_io()
def ask(body: AskRequest):
    """Advance the conversation one turn and persist both turns."""
    LOG.debug(
        "ask input: conversation_id=%s message=%r", body.conversation_id, body.message
    )
    reply = agent.run_agent(body.conversation_id, body.message)
    data.append_turn(body.conversation_id, "user", body.message)
    data.append_turn(body.conversation_id, "assistant", reply)
    LOG.debug("ask output: conversation_id=%s reply=%r", body.conversation_id, reply)
    return {"reply": reply, "conversation_id": body.conversation_id}


@app.post("/callback")
@log_io()
def callback(body: CallbackRequest):
    """Third-party workflow completion hook.

    The mapping table is keyed on ``conversation_id``; the callback also carries
    the ``workflow_id`` so we can confirm this completion belongs to the order
    we recorded (and reject a stale/mismatched workflow). On a match it composes
    the receipt/total message, optionally pushes it to WhatsApp (only when the
    origination phone number is configured and a mapping with a WhatsApp number
    exists), and records the assistant turn. Never crashes when WhatsApp
    env/number is absent.
    """
    LOG.debug(
        "callback input: conversation_id=%s workflow_id=%s receipt_id=%s total_cost=%r",
        body.conversation_id,
        body.workflow_id,
        body.receipt_id,
        body.total_cost,
    )
    mapping = data.get_mapping(body.conversation_id)
    LOG.debug("callback mapping lookup: %r", mapping)

    # Match on conversation_id AND workflow_id. A missing mapping or a
    # workflow_id that doesn't match the recorded one is rejected (404), so a
    # callback for a different/stale workflow never messages the user.
    if not mapping:
        LOG.warning("Callback for unknown conversation %s", body.conversation_id)
        raise HTTPException(status_code=404, detail="conversation not found")
    if mapping.get("workflow_id") != body.workflow_id:
        LOG.warning(
            "Callback workflow_id mismatch for conversation %s (got %s, stored %s)",
            body.conversation_id,
            body.workflow_id,
            mapping.get("workflow_id"),
        )
        raise HTTPException(status_code=404, detail="workflow_id does not match")

    text = (
        f"Your order is complete. Receipt {body.receipt_id}. "
        f"Total cost {body.total_cost}."
    )

    # Route the completion by the channel recorded when the order was placed.
    # - "whatsapp": push the message to the user's WhatsApp number (gated on
    #   the origination phone id being configured).
    # - "api" (UI): nothing is pushed; the message is persisted to chat history
    #   and the UI picks it up via GET /history.
    channel = (mapping.get("channel") or "").lower()
    if channel == "whatsapp":
        if os.environ.get("WHATSAPP_PHONE_NUMBER_ID") and mapping.get("whatsapp_number"):
            try:
                whatsapp.send_whatsapp_message(mapping["whatsapp_number"], text)
            except Exception:  # noqa: BLE001 - delivery must not break the hook
                LOG.exception(
                    "Failed to send WhatsApp callback for conversation %s",
                    body.conversation_id,
                )
        else:
            LOG.warning(
                "Channel is whatsapp but no phone config/number for %s; "
                "message stored to history only",
                body.conversation_id,
            )

    # Always record the assistant turn so it is in history for either channel
    # (and so the UI's Load history button can show it).
    data.append_turn(body.conversation_id, "assistant", text)
    LOG.debug(
        "callback output: conversation_id=%s channel=%s message=%r",
        body.conversation_id,
        channel,
        text,
    )
    return {"status": "ok", "channel": channel}


@app.get("/history")
@log_io()
def history(conversation_id: str):
    """Return the stored conversation turns for ``conversation_id``.

    Used by the test page's "Load history" button so a UI-channel user can see
    the order-completion message the callback stored (the UI has no server
    push). Returns turns oldest-first.
    """
    turns = data.get_history(conversation_id)
    return {
        "conversation_id": conversation_id,
        "turns": [
            {
                "role": t.get("role"),
                "text": t.get("text"),
                "created_at": t.get("created_at"),
            }
            for t in turns
        ],
    }


# --- Lambda dispatch -------------------------------------------------------

handler = Mangum(app)


@log_io()
def process_sqs_batch(event):
    """Handle a WhatsApp SQS batch, isolating per-record failures.

    Reuses the proven ``whatsapp`` extractors and the shared agent core. The
    conversation_id is the WhatsApp sender so the mapping write performed inside
    ``create_order`` stores the phone number for later callbacks. Returns the
    ``batchItemFailures`` shape so SQS only retries the records that failed,
    mirroring handler.py.
    """
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record.get("messageId")
        try:
            webhook = whatsapp.extract_webhook_data(record)
            for msg in whatsapp.extract_text_messages(webhook):
                sender = msg["sender"]
                LOG.debug("sqs message input: sender=%s text=%r", sender, msg["text"])
                reply = agent.run_agent(sender, msg["text"], whatsapp_number=sender)
                data.append_turn(sender, "user", msg["text"])
                data.append_turn(sender, "assistant", reply)
                whatsapp.send_whatsapp_message(sender, reply)
                LOG.debug("sqs message output: sender=%s reply=%r", sender, reply)
        except Exception:  # noqa: BLE001 - isolate per-record failures
            LOG.exception("Failed to process SQS record %s", message_id)
            if message_id:
                batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}


def lambda_handler(event, context):
    """Single entry point: route SQS events to the batch path, else to Mangum."""
    records = event.get("Records") if isinstance(event, dict) else None
    is_sqs = bool(records and records[0].get("eventSource") == "aws:sqs")
    LOG.debug("lambda_handler input: is_sqs=%s event_keys=%s", is_sqs,
              list(event.keys()) if isinstance(event, dict) else type(event).__name__)
    if is_sqs:
        return process_sqs_batch(event)
    return handler(event, context)
