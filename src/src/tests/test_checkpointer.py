"""Offline tests for the DynamoDB-backed LangGraph checkpoint saver."""

import time

import pytest

from checkpointer import DynamoDBSaver, TTL_SECONDS
from conftest import CHECKPOINTER_TABLE_NAME


def _config(thread_id="t-1", checkpoint_ns="", checkpoint_id=None):
    configurable = {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _checkpoint(checkpoint_id="c-1"):
    return {
        "v": 1,
        "id": checkpoint_id,
        "ts": "2024-01-01T00:00:00+00:00",
        "channel_values": {"messages": ["hi"]},
        "channel_versions": {"messages": 1},
        "versions_seen": {},
    }


@pytest.fixture
def saver(dynamodb):
    return DynamoDBSaver(table_name=CHECKPOINTER_TABLE_NAME)


def test_put_then_get_tuple_restores_checkpoint(saver):
    checkpoint = _checkpoint("c-1")
    metadata = {"source": "input", "step": 1}

    out_config = saver.put(_config(), checkpoint, metadata, {})
    assert out_config["configurable"]["checkpoint_id"] == "c-1"

    restored = saver.get_tuple(_config())
    assert restored is not None
    assert restored.checkpoint == checkpoint
    assert restored.metadata == metadata
    assert restored.config["configurable"]["checkpoint_id"] == "c-1"


def test_put_writes_then_get_tuple_returns_pending_writes(saver):
    checkpoint = _checkpoint("c-1")
    saver.put(_config(), checkpoint, {"step": 1}, {})

    write_config = _config(checkpoint_id="c-1")
    saver.put_writes(
        write_config,
        [("messages", "value-a"), ("other", "value-b")],
        task_id="task-1",
    )

    restored = saver.get_tuple(_config())
    assert restored is not None
    channels = {channel for _, channel, _ in restored.pending_writes}
    values = {value for _, _, value in restored.pending_writes}
    assert channels == {"messages", "other"}
    assert values == {"value-a", "value-b"}


def test_stored_items_carry_expires_at_ttl(saver, dynamodb):
    before = int(time.time())
    saver.put(_config(), _checkpoint("c-1"), {"step": 1}, {})
    after = int(time.time())

    table = dynamodb.Table(CHECKPOINTER_TABLE_NAME)
    item = table.get_item(
        Key={"pk": "t-1::", "sk": "checkpoint::c-1"}
    )["Item"]

    expires_at = int(item["expires_at"])
    assert before + TTL_SECONDS <= expires_at <= after + TTL_SECONDS


def test_list_returns_newest_first(saver):
    saver.put(_config(), _checkpoint("c-1"), {"step": 1}, {})
    saver.put(_config(checkpoint_id="c-1"), _checkpoint("c-2"), {"step": 2}, {})

    ids = [t.checkpoint["id"] for t in saver.list(_config())]
    assert ids == ["c-2", "c-1"]
