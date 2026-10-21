import json
import os
import logging
import boto3

from botocore.exceptions import ClientError

# =========================================================
# CONFIGURATION
# =========================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

RAG_LAMBDA_NAME = os.environ.get(
    "RAG_LAMBDA_NAME",
    "Query_Lambda_RAG_Ayman"
)

WHATSAPP_PHONE_NUMBER_ID = os.environ.get(
    "WHATSAPP_PHONE_NUMBER_ID"
)

META_API_VERSION = os.environ.get(
    "META_API_VERSION",
    "v20.0"
)

# =========================================================
# AWS CLIENTS
# =========================================================

lambda_client = boto3.client(
    "lambda",
    region_name=AWS_REGION
)

social_client = boto3.client(
    "socialmessaging",
    region_name=AWS_REGION
)

# =========================================================
# JSON PARSER
# =========================================================

def parse_json_if_string(value):
    """
    Parse JSON when the input is a string.
    Otherwise, return the input unchanged.
    """

    if isinstance(value, str):
        return json.loads(value)

    return value

# =========================================================
# EXTRACT WEBHOOK FROM SQS
# =========================================================

def extract_webhook_data(sqs_record):
    """
    Expected formats:

    SQS -> SNS -> WhatsApp webhook

    OR

    SQS -> WhatsApp webhook directly
    """

    body = json.loads(sqs_record["body"])

    # Handle SNS notification format
    if isinstance(body, dict) and "Message" in body:

        body = body["Message"]

        if isinstance(body, str):
            body = json.loads(body)

    if not isinstance(body, dict):
        raise ValueError(
            "Webhook body is not a JSON object"
        )

    # Diagnostic logs: do not log phone numbers or message text
    logger.info(
        "Webhook root type: %s, keys: %s",
        type(body).__name__,
        list(body.keys())
    )

    entries = body.get("whatsAppWebhookEntry")

    logger.info(
        "Webhook entries type: %s",
        type(entries).__name__
    )

    if isinstance(entries, list) and entries:

        logger.info(
            "First webhook entry type: %s",
            type(entries[0]).__name__
        )

        if isinstance(entries[0], dict):

            logger.info(
                "First webhook entry keys: %s",
                list(entries[0].keys())
            )

    return body

# =========================================================
# EXTRACT INCOMING WHATSAPP TEXT MESSAGES
# =========================================================

def extract_text_messages(webhook):
    """
    Extract incoming text messages.

    Handles:
    - Dictionary entries
    - JSON-string entries
    - Dictionary changes
    - JSON-string changes

    Ignores:
    - WhatsApp status notifications
    - Non-text messages
    - Invalid entries
    """

    messages_to_process = []

    entries = webhook.get(
        "whatsAppWebhookEntry",
        []
    )

    # Parse entries if serialized as JSON
    try:
        entries = parse_json_if_string(entries)

    except json.JSONDecodeError:
        logger.error(
            "Webhook entries contain invalid JSON"
        )
        raise

    # Support one entry instead of a list
    if isinstance(entries, dict):
        entries = [entries]

    if not isinstance(entries, list):

        logger.error(
            "Unexpected webhook entries type: %s",
            type(entries).__name__
        )

        raise ValueError(
            "Webhook entries must be a list or dictionary"
        )

    # -----------------------------------------------------
    # Process entries
    # -----------------------------------------------------

    for entry in entries:

        try:
            entry = parse_json_if_string(entry)

        except json.JSONDecodeError:

            logger.warning(
                "Skipping entry containing invalid JSON"
            )

            continue

        if not isinstance(entry, dict):

            logger.warning(
                "Skipping webhook entry with type: %s",
                type(entry).__name__
            )

            continue

        changes = entry.get(
            "changes",
            []
        )

        try:
            changes = parse_json_if_string(changes)

        except json.JSONDecodeError:

            logger.warning(
                "Skipping changes containing invalid JSON"
            )

            continue

        # Support one change instead of a list
        if isinstance(changes, dict):
            changes = [changes]

        if not isinstance(changes, list):

            logger.warning(
                "Unexpected changes type: %s",
                type(changes).__name__
            )

            continue

        # -------------------------------------------------
        # Process changes
        # -------------------------------------------------

        for change in changes:

            try:
                change = parse_json_if_string(change)

            except json.JSONDecodeError:

                logger.warning(
                    "Skipping change containing invalid JSON"
                )

                continue

            if not isinstance(change, dict):
                continue

            value = change.get(
                "value",
                {}
            )

            try:
                value = parse_json_if_string(value)

            except json.JSONDecodeError:

                logger.warning(
                    "Skipping value containing invalid JSON"
                )

                continue

            if not isinstance(value, dict):
                continue

            messages = value.get(
                "messages",
                []
            )

            try:
                messages = parse_json_if_string(messages)

            except json.JSONDecodeError:

                logger.warning(
                    "Skipping messages containing invalid JSON"
                )

                continue

            # Support a single message instead of a list
            if isinstance(messages, dict):
                messages = [messages]

            if not isinstance(messages, list):
                continue

            # ---------------------------------------------
            # Process incoming messages
            # ---------------------------------------------

            for message in messages:

                if not isinstance(message, dict):
                    continue

                message_type = message.get("type")

                # Ignore non-text messages
                if message_type != "text":

                    logger.info(
                        "Skipping unsupported message type: %s",
                        message_type
                    )

                    continue

                sender = message.get("from")

                incoming_message_id = message.get("id")

                text_object = message.get(
                    "text",
                    {}
                )

                if not isinstance(text_object, dict):
                    text_object = {}

                incoming_text = (
                    text_object.get("body", "")
                    .strip()
                )

                if not sender or not incoming_text:

                    logger.warning(
                        "Incoming message has no sender or text"
                    )

                    continue

                messages_to_process.append({
                    "sender": sender,
                    "message_id": incoming_message_id,
                    "text": incoming_text
                })

    return messages_to_process

# =========================================================
# CALL RAG LAMBDA
# =========================================================

def call_rag_lambda(question):
    """
    Invoke Query_Lambda_RAG_Ayman synchronously.

    Supports:
    - Lambda proxy response with statusCode and body
    - Direct JSON response containing answer
    """

    payload = {
        "question": question,
        "conversation_history": []
    }

    logger.info("Calling RAG Lambda")

    response = lambda_client.invoke(
        FunctionName=RAG_LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8")
    )

    raw_response = response["Payload"].read()

    # Check Lambda execution errors
    if response.get("FunctionError"):

        logger.error(
            "RAG Lambda execution failed: %s",
            raw_response.decode(
                "utf-8",
                errors="replace"
            )
        )

        raise RuntimeError(
            "RAG Lambda execution failed"
        )

    if not raw_response:

        raise RuntimeError(
            "RAG Lambda returned an empty response"
        )

    try:
        rag_response = json.loads(raw_response)

    except json.JSONDecodeError:

        logger.error(
            "RAG Lambda returned invalid JSON"
        )

        raise

    # -----------------------------------------------------
    # Handle Lambda proxy response
    # -----------------------------------------------------

    if (
        isinstance(rag_response, dict)
        and "body" in rag_response
    ):

        status_code = rag_response.get(
            "statusCode",
            200
        )

        if status_code != 200:

            logger.error(
                "RAG Lambda returned status code: %s",
                status_code
            )

            raise RuntimeError(
                f"RAG Lambda returned status code {status_code}"
            )

        body = rag_response["body"]

        if isinstance(body, str):

            rag_result = json.loads(body)

        else:

            rag_result = body

    else:

        # Direct response format
        rag_result = rag_response

    if not isinstance(rag_result, dict):

        raise RuntimeError(
            "Unexpected RAG response format"
        )

    answer = (
        rag_result.get("answer")
        or rag_result.get("response")
        or rag_result.get("text")
    )

    if not answer:

        logger.error(
            "RAG response did not contain an answer"
        )

        raise RuntimeError(
            "RAG Lambda returned no answer"
        )

    return str(answer).strip()

# =========================================================
# NORMALIZE WHATSAPP RECIPIENT
# =========================================================

def normalize_destination(phone_number):
    """
    Normalize the WhatsApp recipient.

    The sender number from the incoming WhatsApp
    webhook is expected to be an international number.
    """

    if not phone_number:

        raise ValueError(
            "WhatsApp recipient is missing"
        )

    phone_number = str(
        phone_number
    ).strip()

    # Remove common formatting characters
    phone_number = (
        phone_number
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )

    # Convert international dialing prefix 00
    if phone_number.startswith("00"):

        phone_number = (
            "+"
            + phone_number[2:]
        )

    # Preserve existing +
    elif phone_number.startswith("+"):
        pass

    # Add + to a number containing digits only
    elif phone_number.isdigit():

        phone_number = (
            "+"
            + phone_number
        )

    else:

        raise ValueError(
            "Invalid WhatsApp recipient format"
        )

    return phone_number

# =========================================================
# SEND WHATSAPP MESSAGE
# =========================================================

def send_whatsapp_message(destination, text):
    """
    Submit the response using AWS End User
    Messaging Social.
    """

    if not WHATSAPP_PHONE_NUMBER_ID:

        raise RuntimeError(
            "WHATSAPP_PHONE_NUMBER_ID environment variable is missing"
        )

    if not text:

        raise ValueError(
            "Cannot send an empty WhatsApp message"
        )

    destination = normalize_destination(
        destination
    )

    message_data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": destination,
        "type": "text",
        "text": {
            "body": text
        }
    }

    logger.info(
        "Submitting WhatsApp reply. "
        "Recipient suffix: %s",
        destination[-4:]
    )

    response = social_client.send_whatsapp_message(
        originationPhoneNumberId=WHATSAPP_PHONE_NUMBER_ID,
        metaApiVersion=META_API_VERSION,
        message=json.dumps(message_data)
    )

    message_id = response.get(
        "messageId"
    )

    logger.info(
        "WhatsApp reply submitted. Message ID: %s",
        message_id
    )

    return response

# =========================================================
# PROCESS ONE SQS RECORD
# =========================================================

def process_sqs_record(record):

    webhook = extract_webhook_data(
        record
    )

    incoming_messages = extract_text_messages(
        webhook
    )

    # Status-only notifications are not errors
    if not incoming_messages:

        logger.info(
            "Skipping webhook event: no incoming text messages"
        )

        return

    logger.info(
        "Extracted %s incoming text message(s)",
        len(incoming_messages)
    )

    # Process each text message
    for incoming in incoming_messages:

        sender = incoming["sender"]

        incoming_text = incoming["text"]

        incoming_message_id = incoming["message_id"]

        logger.info(
            "Processing WhatsApp message. "
            "Message ID present: %s, Sender present: %s",
            bool(incoming_message_id),
            bool(sender)
        )

        # 1. Get the answer from RAG
        answer = call_rag_lambda(
            incoming_text
        )

        logger.info(
            "RAG Lambda returned successfully"
        )

        # 2. Send the answer to the original sender
        response = send_whatsapp_message(
            destination=sender,
            text=answer
        )
        logger.info(
            "Output Send to Whatsapp"
            "Response: %s",
            str(response)
        )

# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(event, context):
    records = event.get(
        "Records",
        []
    )

    logger.info(
        "Received %s SQS records",
        len(records)
    )

    batch_item_failures = []

    for record in records:

        sqs_message_id = record.get(
            "messageId"
        )

        try:

            process_sqs_record(
                record
            )

        except Exception:

            logger.exception(
                "Failed to process SQS record: %s",
                sqs_message_id
            )

            # Report this record as failed
            # so SQS can retry it
            if sqs_message_id:

                batch_item_failures.append({
                    "itemIdentifier": sqs_message_id
                })

    return {
        "batchItemFailures": batch_item_failures
    }
