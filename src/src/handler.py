"""Sample message-processor Lambda for the WhatsApp -> AI -> RPA pipeline.

This is a REFERENCE implementation, deliberately kept dependency-free (only the
AWS SDK `boto3`, which is bundled in the Lambda Python runtime). It demonstrates
the end-to-end flow and the exact integration points. Replace the business logic
in `invoke_rpa` and tune the Bedrock request payload for your chosen model.

Flow per SQS record:
    1. Parse the inbound WhatsApp notification (delivered via SNS -> SQS).
    2. Load/append conversation state in DynamoDB.
    3. Ask Amazon Bedrock (model or agent) for a response.
    4. Hand the AI output to the external RPA system.
    5. Persist the exchange and return partial-batch-failure info to SQS.

Configuration is passed entirely through environment variables set by Terraform
(see modules/message-processor and the root main.tf), so no value is hardcoded.
"""

import json
import logging
import os
import time
import urllib.request
import uuid

import boto3

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# --- Configuration from environment (injected by Terraform) ----------------
TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
BEDROCK_ENABLED = os.environ.get("BEDROCK_ENABLED", "false").lower() == "true"
BEDROCK_MODE = os.environ.get("BEDROCK_MODE", "model")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "")
BEDROCK_AGENT_ID = os.environ.get("BEDROCK_AGENT_ID", "")
BEDROCK_AGENT_ALIAS = os.environ.get("BEDROCK_AGENT_ALIAS", "")
RPA_API_ENDPOINT = os.environ.get("RPA_API_ENDPOINT", "")
RPA_SECRET_ARN = os.environ.get("RPA_SECRET_ARN", "")
CONVERSATION_TTL_SECONDS = int(os.environ.get("CONVERSATION_TTL_SECONDS", "2592000"))  # 30d

# --- AWS clients (module scope so they are reused across invocations) -------
_dynamodb = boto3.resource("dynamodb")
_table = _dynamodb.Table(TABLE_NAME)
_bedrock_runtime = boto3.client("bedrock-runtime") if BEDROCK_ENABLED and BEDROCK_MODE == "model" else None
_bedrock_agent_runtime = boto3.client("bedrock-agent-runtime") if BEDROCK_ENABLED and BEDROCK_MODE == "agent" else None
_secrets = boto3.client("secretsmanager") if RPA_SECRET_ARN else None

_rpa_secret_cache = None


def lambda_handler(event, context):
    """Entry point. Processes an SQS batch and reports per-item failures."""
    failures = []
    for record in event.get("Records", []):
        message_id = record.get("messageId")
        try:
            _process_record(record)
        except Exception:  # noqa: BLE001 - we want to isolate per-message failures
            LOG.exception("Failed to process SQS record %s", message_id)
            failures.append({"itemIdentifier": message_id})

    # ReportBatchItemFailures: only failed messages return to the queue.
    return {"batchItemFailures": failures}


def _process_record(record):
    body = _parse_body(record.get("body", "{}"))
    conversation_id, user_text = _extract_message(body)

    LOG.debug("Processing conversation=%s text=%s", conversation_id, user_text)

    # 1. Persist the inbound user turn.
    _put_turn(conversation_id, role="user", text=user_text)

    # 2. Ask the AI layer for a reply.
    ai_reply = _generate_ai_reply(conversation_id, user_text)

    # 3. Persist the AI turn.
    _put_turn(conversation_id, role="assistant", text=ai_reply)

    # 4. Trigger the external RPA business process with the AI output.
    if RPA_API_ENDPOINT:
        invoke_rpa(conversation_id, user_text, ai_reply)


def _parse_body(raw):
    """SNS may deliver raw (raw_message_delivery=true) or an SNS envelope."""
    data = json.loads(raw)
    if isinstance(data, dict) and "Message" in data and "Type" in data:
        # SNS envelope - unwrap the inner message.
        try:
            return json.loads(data["Message"])
        except (TypeError, ValueError):
            return {"text": data["Message"]}
    return data


def _extract_message(body):
    """Map an End User Messaging Social WhatsApp payload to (conversation_id, text).

    The exact shape depends on the WhatsApp webhook payload forwarded by End User
    Messaging Social. This extractor is defensive and falls back gracefully so
    the sample runs even with a simple {"from": "...", "text": "..."} test event.
    """
    sender = (
        body.get("from")
        or body.get("sender")
        or body.get("whatsAppUserId")
        or "unknown-sender"
    )
    text = body.get("text")
    if isinstance(text, dict):  # WhatsApp text messages nest under {"body": "..."}
        text = text.get("body", "")
    if not text:
        text = body.get("message", "")
    return sender, text or ""


def _generate_ai_reply(conversation_id, user_text):
    if not BEDROCK_ENABLED:
        return "AI disabled; echoing: " + user_text

    if BEDROCK_MODE == "agent":
        return _invoke_bedrock_agent(conversation_id, user_text)
    return _invoke_bedrock_model(user_text)


def _invoke_bedrock_model(user_text):
    """Invoke a foundation model. Payload shown for Anthropic Claude messages API."""
    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": user_text}],
    }
    resp = _bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(payload),
        contentType="application/json",
        accept="application/json",
    )
    data = json.loads(resp["body"].read())
    # Claude messages API returns content as a list of blocks.
    blocks = data.get("content", [])
    return "".join(b.get("text", "") for b in blocks) or json.dumps(data)


def _invoke_bedrock_agent(conversation_id, user_text):
    """Invoke a Bedrock Agent using the conversation_id as the session id."""
    resp = _bedrock_agent_runtime.invoke_agent(
        agentId=BEDROCK_AGENT_ID,
        agentAliasId=BEDROCK_AGENT_ALIAS,
        sessionId=conversation_id,
        inputText=user_text,
    )
    # The response is an event stream; concatenate chunk bytes.
    chunks = []
    for event in resp.get("completion", []):
        chunk = event.get("chunk", {})
        if "bytes" in chunk:
            chunks.append(chunk["bytes"].decode("utf-8"))
    return "".join(chunks)


def invoke_rpa(conversation_id, user_text, ai_reply):
    """INTEGRATION POINT: call the external RPA system.

    This uses urllib so the sample has zero third-party dependencies. Swap for
    `requests` or an RPA-vendor SDK in a real deployment. Authentication comes
    from Secrets Manager (ARN injected by Terraform) and is never hardcoded.
    """
    headers = {"Content-Type": "application/json"}
    secret = _get_rpa_secret()
    if secret and secret.get("api_key"):
        headers["Authorization"] = f"Bearer {secret['api_key']}"

    payload = json.dumps(
        {
            "conversationId": conversation_id,
            "userMessage": user_text,
            "aiResponse": ai_reply,
        }
    ).encode("utf-8")

    req = urllib.request.Request(RPA_API_ENDPOINT, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as http_resp:  # noqa: S310
            LOG.info("RPA call status=%s", http_resp.status)
    except Exception:  # noqa: BLE001
        LOG.exception("RPA invocation failed for conversation %s", conversation_id)
        raise


def _get_rpa_secret():
    global _rpa_secret_cache
    if not RPA_SECRET_ARN:
        return None
    if _rpa_secret_cache is None:
        value = _secrets.get_secret_value(SecretId=RPA_SECRET_ARN)["SecretString"]
        try:
            _rpa_secret_cache = json.loads(value)
        except ValueError:
            _rpa_secret_cache = {"api_key": value}
    return _rpa_secret_cache


def _put_turn(conversation_id, role, text):
    """Append a conversation turn to DynamoDB with a TTL-based expiry."""
    now = int(time.time())
    _table.put_item(
        Item={
            "conversation_id": conversation_id,
            "message_timestamp": f"{now}-{uuid.uuid4().hex[:8]}",
            "role": role,
            "text": text,
            "created_at": now,
            "expires_at": now + CONVERSATION_TTL_SECONDS,
        }
    )
