"""Offline tests for the env-driven DynamoDB access layer in data.py."""

import boto3
import pytest

import data
from conftest import USERS_TABLE_NAME


@pytest.fixture(autouse=True)
def _reset_data_cache():
    """Reset data.py's cached boto3 handles so each test binds to moto."""
    data._dynamodb = None
    data._tables = {}
    yield
    data._dynamodb = None
    data._tables = {}


def test_is_authorized_user_true(dynamodb):
    dynamodb.Table(USERS_TABLE_NAME).put_item(Item={"user_id": "u-123"})
    assert data.is_authorized_user("u-123") is True


def test_is_authorized_user_false(dynamodb):
    assert data.is_authorized_user("missing") is False


def test_append_turn_writes_item(dynamodb):
    item = data.append_turn("conv-1", "user", "hello there")

    stored = dynamodb.Table("test-chat-history").get_item(
        Key={"conversation_id": "conv-1", "sk": item["sk"]}
    )["Item"]

    assert stored["conversation_id"] == "conv-1"
    assert stored["role"] == "user"
    assert stored["text"] == "hello there"
    assert "created_at" in stored


def test_put_and_get_mapping_round_trip(dynamodb):
    data.put_mapping(
        conversation_id="conv-9",
        workflow_id="wf-42",
        customer_id="C-123",
        whatsapp_number="+15551234567",
        channel="whatsapp",
    )

    mapping = data.get_mapping("conv-9")
    assert mapping == {
        "conversation_id": "conv-9",
        "workflow_id": "wf-42",
        "customer_id": "C-123",
        "whatsapp_number": "+15551234567",
        "channel": "whatsapp",
    }


def test_get_mapping_missing_returns_none(dynamodb):
    assert data.get_mapping("nope") is None


def test_get_history_returns_turns_in_order(dynamodb):
    data.append_turn("conv-h", "user", "first")
    data.append_turn("conv-h", "assistant", "second")
    data.append_turn("conv-h", "user", "third")

    turns = data.get_history("conv-h")
    assert [t["text"] for t in turns] == ["first", "second", "third"]
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]


def test_get_history_empty_for_unknown_conversation(dynamodb):
    assert data.get_history("nobody") == []
