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

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from mangum import Mangum
from pydantic import BaseModel

import agent
import data
import whatsapp

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

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
def index():
    """Serve the vanilla-JS test page that sits next to this module."""
    with open(_TEST_PAGE, "r", encoding="utf-8") as handle:
        return HTMLResponse(content=handle.read())


@app.post("/ask")
def ask(body: AskRequest):
    """Advance the conversation one turn and persist both turns."""
    reply = agent.run_agent(body.conversation_id, body.message)
    data.append_turn(body.conversation_id, "user", body.message)
    data.append_turn(body.conversation_id, "assistant", reply)
    return {"reply": reply, "conversation_id": body.conversation_id}


@app.post("/callback")
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
    mapping = data.get_mapping(body.conversation_id)

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

    if os.environ.get("WHATSAPP_PHONE_NUMBER_ID") and mapping.get("whatsapp_number"):
        try:
            whatsapp.send_whatsapp_message(mapping["whatsapp_number"], text)
        except Exception:  # noqa: BLE001 - WhatsApp delivery must not break the hook
            LOG.exception(
                "Failed to send WhatsApp callback for conversation %s",
                body.conversation_id,
            )

    data.append_turn(body.conversation_id, "assistant", text)
    return {"status": "ok"}


# --- Lambda dispatch -------------------------------------------------------

handler = Mangum(app)


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
                reply = agent.run_agent(sender, msg["text"], whatsapp_number=sender)
                data.append_turn(sender, "user", msg["text"])
                data.append_turn(sender, "assistant", reply)
                whatsapp.send_whatsapp_message(sender, reply)
        except Exception:  # noqa: BLE001 - isolate per-record failures
            LOG.exception("Failed to process SQS record %s", message_id)
            if message_id:
                batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}


def lambda_handler(event, context):
    """Single entry point: route SQS events to the batch path, else to Mangum."""
    records = event.get("Records") if isinstance(event, dict) else None
    if records and records[0].get("eventSource") == "aws:sqs":
        return process_sqs_batch(event)
    return handler(event, context)
