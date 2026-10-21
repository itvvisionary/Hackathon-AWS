"""Env-driven DynamoDB access for the four tables used by the agent Lambda.

Table names and region come entirely from environment variables so nothing is
hardcoded. A single ``boto3.resource('dynamodb')`` is created lazily and the
individual ``Table`` handles are cached on first use. Deferring resource/table
creation to first use lets tests set the environment (and start moto) before
any AWS client is constructed.
"""

import os
import time
import uuid

import boto3

from logging_setup import get_logger, log_io

LOG = get_logger(__name__)


def _region():
    return os.environ.get("AWS_REGION", "us-east-1")


# --- Lazily created, cached boto3 handles ----------------------------------
_dynamodb = None
_tables = {}


def _resource():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=_region())
    return _dynamodb


def _table(env_var):
    """Return a cached Table handle for the table named by ``env_var``.

    The name is ``.strip()``-ed so a stray leading/trailing space in the
    environment variable (easy to introduce when pasting into the Lambda
    console) does not reach DynamoDB, whose table names forbid spaces
    (pattern ``[a-zA-Z0-9_.-]+``).
    """
    name = os.environ[env_var].strip()
    cached = _tables.get(name)
    if cached is None:
        cached = _resource().Table(name)
        _tables[name] = cached
    return cached


def _users_table():
    return _table("USERS_TABLE_NAME")


def _chat_history_table():
    return _table("CHAT_HISTORY_TABLE_NAME")


def _mapping_table():
    return _table("MAPPING_TABLE_NAME")


# --- Users -----------------------------------------------------------------

@log_io()
def is_authorized_user(user_id):
    """Return True if ``user_id`` exists in the users table (PK ``user_id``)."""
    response = _users_table().get_item(Key={"user_id": user_id})
    return "Item" in response


@log_io()
def get_user(user_id):
    """Return the full users-table item for ``user_id`` or ``None`` if absent.

    The item may carry an optional stored profile (``user_name``,
    ``user_email``, ``user_address``) used to prefill the order's ship-to
    details. Only ``user_id`` (the key) is guaranteed to exist.
    """
    response = _users_table().get_item(Key={"user_id": user_id})
    return response.get("Item")


# --- Chat history ----------------------------------------------------------

@log_io()
def append_turn(conversation_id, role, text):
    """Append one conversation turn to the chat-history table."""
    now = int(time.time())
    sk = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    item = {
        "conversation_id": conversation_id,
        "sk": sk,
        "role": role,
        "text": text,
        "created_at": now,
    }
    _chat_history_table().put_item(Item=item)
    return item


@log_io()
def get_history(conversation_id):
    """Return all turns for ``conversation_id`` in chronological order.

    Each turn is ``{"role", "text", "created_at", "sk"}``. The ``sk`` sort key
    is time-ordered, so querying with ``ScanIndexForward=True`` yields the
    conversation oldest-first.
    """
    from boto3.dynamodb.conditions import Key

    items = []
    kwargs = {
        "KeyConditionExpression": Key("conversation_id").eq(conversation_id),
        "ScanIndexForward": True,
    }
    table = _chat_history_table()
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return items


# --- Conversation -> workflow mapping --------------------------------------

@log_io()
def put_mapping(conversation_id, workflow_id, user_id, whatsapp_number, channel):
    """Store the conversation->workflow mapping (PK ``conversation_id``).

    ``channel`` records how the order was placed (``"whatsapp"`` or ``"api"``)
    so the callback can route the completion message back the same way.
    """
    item = {
        "conversation_id": conversation_id,
        "workflow_id": workflow_id,
        "user_id": user_id,
        "whatsapp_number": whatsapp_number,
        "channel": channel,
    }
    _mapping_table().put_item(Item=item)
    return item


@log_io()
def get_mapping(conversation_id):
    """Return the mapping item for ``conversation_id`` or None if absent."""
    response = _mapping_table().get_item(Key={"conversation_id": conversation_id})
    return response.get("Item")
