"""Self-contained WhatsApp helpers (AWS End User Messaging Social).

This module owns the WhatsApp send/parse logic directly — it no longer imports
from ``whatsapp_lambda``. The behavior mirrors the original reference
implementation: inbound webhook extraction from an SQS record, recipient
normalization, and sending a text reply via the ``socialmessaging`` client.

Every setting comes from the environment; nothing is hardcoded.

Public surface:
    - extract_webhook_data(sqs_record)  -> dict
    - extract_text_messages(webhook)    -> list[{"sender","message_id","text"}]
    - normalize_destination(number)     -> "+E164" string
    - send_whatsapp_message(dest, text) -> socialmessaging response
"""

import json
import os

import boto3

from logging_setup import get_logger, log_io

LOG = get_logger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
META_API_VERSION = os.environ.get("META_API_VERSION", "v20.0")

# Module-scope client for warm-container reuse. Created lazily so importing this
# module (e.g. in tests or the graph-export script) never requires AWS wiring.
_social_client = None


def _social():
    global _social_client
    if _social_client is None:
        _social_client = boto3.client("socialmessaging", region_name=AWS_REGION)
    return _social_client


# =========================================================
# JSON PARSER
# =========================================================

def parse_json_if_string(value):
    """Parse JSON when the input is a string; otherwise return it unchanged."""
    if isinstance(value, str):
        return json.loads(value)
    return value


# =========================================================
# EXTRACT WEBHOOK FROM SQS
# =========================================================

@log_io()
def extract_webhook_data(sqs_record):
    """Return the webhook dict from an SQS record.

    Handles both ``SQS -> SNS -> WhatsApp webhook`` (SNS envelope with a
    ``Message`` field) and ``SQS -> WhatsApp webhook`` delivered directly.

    Returns ``None`` for bodies that are not a JSON object (for example a plain
    text test message published from the SNS/SQS console). Such a message can
    never succeed, so retrying it would only send it to the DLQ; the caller
    skips it instead. A short preview is logged to show what was received.
    """
    raw = sqs_record.get("body") or ""
    try:
        body = json.loads(raw)

        # Handle SNS notification envelope
        if isinstance(body, dict) and "Message" in body:
            body = body["Message"]
            if isinstance(body, str):
                body = json.loads(body)
    except json.JSONDecodeError:
        LOG.warning(
            "Skipping SQS message %s: body is not JSON (length %d, starts with %r)",
            sqs_record.get("messageId"),
            len(raw),
            raw[:200],
        )
        return None

    if not isinstance(body, dict):
        LOG.warning(
            "Skipping SQS message %s: body is JSON %s, not an object",
            sqs_record.get("messageId"),
            type(body).__name__,
        )
        return None

    LOG.info(
        "Webhook root type: %s, keys: %s",
        type(body).__name__,
        list(body.keys()),
    )
    return body


# =========================================================
# EXTRACT INCOMING WHATSAPP TEXT MESSAGES
# =========================================================

@log_io()
def extract_text_messages(webhook):
    """Extract incoming text messages from a WhatsApp webhook.

    Returns a list of dicts: ``{"sender", "message_id", "text"}``. Handles dict
    or JSON-string entries/changes/values, supports single items in place of
    lists, and ignores status notifications and non-text messages.
    """
    messages_to_process = []

    entries = webhook.get("whatsAppWebhookEntry", [])
    try:
        entries = parse_json_if_string(entries)
    except json.JSONDecodeError:
        LOG.error("Webhook entries contain invalid JSON")
        raise

    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        raise ValueError("Webhook entries must be a list or dictionary")

    for entry in entries:
        try:
            entry = parse_json_if_string(entry)
        except json.JSONDecodeError:
            LOG.warning("Skipping entry containing invalid JSON")
            continue
        if not isinstance(entry, dict):
            continue

        changes = entry.get("changes", [])
        try:
            changes = parse_json_if_string(changes)
        except json.JSONDecodeError:
            LOG.warning("Skipping changes containing invalid JSON")
            continue
        if isinstance(changes, dict):
            changes = [changes]
        if not isinstance(changes, list):
            continue

        for change in changes:
            try:
                change = parse_json_if_string(change)
            except json.JSONDecodeError:
                LOG.warning("Skipping change containing invalid JSON")
                continue
            if not isinstance(change, dict):
                continue

            value = change.get("value", {})
            try:
                value = parse_json_if_string(value)
            except json.JSONDecodeError:
                LOG.warning("Skipping value containing invalid JSON")
                continue
            if not isinstance(value, dict):
                continue

            messages = value.get("messages", [])
            try:
                messages = parse_json_if_string(messages)
            except json.JSONDecodeError:
                LOG.warning("Skipping messages containing invalid JSON")
                continue
            if isinstance(messages, dict):
                messages = [messages]
            if not isinstance(messages, list):
                continue

            for message in messages:
                if not isinstance(message, dict):
                    continue

                # Ignore non-text messages (status notifications, media, etc.)
                if message.get("type") != "text":
                    LOG.info(
                        "Skipping unsupported message type: %s",
                        message.get("type"),
                    )
                    continue

                sender = message.get("from")
                incoming_message_id = message.get("id")
                text_object = message.get("text", {})
                if not isinstance(text_object, dict):
                    text_object = {}
                incoming_text = text_object.get("body", "").strip()

                if not sender or not incoming_text:
                    LOG.warning("Incoming message has no sender or text")
                    continue

                messages_to_process.append(
                    {
                        "sender": sender,
                        "message_id": incoming_message_id,
                        "text": incoming_text,
                    }
                )

    return messages_to_process


# =========================================================
# NORMALIZE WHATSAPP RECIPIENT
# =========================================================

def normalize_destination(phone_number):
    """Normalize a WhatsApp recipient to an international ``+E164`` string."""
    if not phone_number:
        raise ValueError("WhatsApp recipient is missing")

    phone_number = str(phone_number).strip()
    phone_number = (
        phone_number.replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )

    if phone_number.startswith("00"):
        phone_number = "+" + phone_number[2:]
    elif phone_number.startswith("+"):
        pass
    elif phone_number.isdigit():
        phone_number = "+" + phone_number
    else:
        raise ValueError("Invalid WhatsApp recipient format")

    return phone_number


# =========================================================
# SEND WHATSAPP MESSAGE
# =========================================================

@log_io()
def send_whatsapp_message(destination, text):
    """Send a text reply via AWS End User Messaging Social."""
    if not WHATSAPP_PHONE_NUMBER_ID:
        raise RuntimeError(
            "WHATSAPP_PHONE_NUMBER_ID environment variable is missing"
        )
    if not text:
        raise ValueError("Cannot send an empty WhatsApp message")

    destination = normalize_destination(destination)

    message_data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": destination,
        "type": "text",
        "text": {"body": text},
    }

    LOG.info("Submitting WhatsApp reply. Recipient suffix: %s", destination[-4:])

    response = _social().send_whatsapp_message(
        originationPhoneNumberId=WHATSAPP_PHONE_NUMBER_ID,
        metaApiVersion=META_API_VERSION,
        message=json.dumps(message_data),
    )

    LOG.info("WhatsApp reply submitted. Message ID: %s", response.get("messageId"))
    return response


__all__ = [
    "parse_json_if_string",
    "extract_webhook_data",
    "extract_text_messages",
    "normalize_destination",
    "send_whatsapp_message",
]
