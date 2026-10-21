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
    """Return a cached Table handle for the table named by ``env_var``."""
    name = os.environ[env_var]
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

def is_authorized_user(user_id):
    """Return True if ``user_id`` exists in the users table (PK ``user_id``)."""
    response = _users_table().get_item(Key={"user_id": user_id})
    return "Item" in response


def get_user(user_id):
    """Return the full users-table item for ``user_id`` or ``None`` if absent.

    The item may carry an optional stored profile (``user_name``,
    ``user_email``, ``user_address``) used to prefill the order's ship-to
    details. Only ``user_id`` (the key) is guaranteed to exist.
    """
    response = _users_table().get_item(Key={"user_id": user_id})
    return response.get("Item")


# --- Chat history ----------------------------------------------------------

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


# --- Conversation -> workflow mapping --------------------------------------

def put_mapping(conversation_id, workflow_id, user_id, whatsapp_number):
    """Store the conversation->workflow mapping (PK ``conversation_id``)."""
    item = {
        "conversation_id": conversation_id,
        "workflow_id": workflow_id,
        "user_id": user_id,
        "whatsapp_number": whatsapp_number,
    }
    _mapping_table().put_item(Item=item)
    return item


def get_mapping(conversation_id):
    """Return the mapping item for ``conversation_id`` or None if absent."""
    response = _mapping_table().get_item(Key={"conversation_id": conversation_id})
    return response.get("Item")
