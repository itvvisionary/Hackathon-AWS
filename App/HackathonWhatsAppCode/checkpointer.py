"""A LangGraph checkpoint saver backed by a single DynamoDB table.

The saver persists checkpoints and pending writes in one table keyed by a
composite (pk, sk):

    pk = f"{thread_id}::{checkpoint_ns}"

    checkpoint item   sk = f"checkpoint::{checkpoint_id}"
    write item        sk = f"write::{checkpoint_id}::{task_id}::{idx}"

Every item carries ``expires_at = int(time.time()) + 604800`` (7 days) so a
DynamoDB TTL on that attribute reclaims old checkpoints automatically. Table
name, like every other setting, comes from the environment and is never
hardcoded.
"""

import os
import time

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import Binary

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_id,
)

from logging_setup import get_logger

LOG = get_logger(__name__)

TTL_SECONDS = 604800  # 7 days


def _expires_at():
    return int(time.time()) + TTL_SECONDS


def _as_bytes(value):
    """Normalize a DynamoDB binary attribute back to ``bytes``."""
    if isinstance(value, Binary):
        return bytes(value)
    return value


class DynamoDBSaver(BaseCheckpointSaver):
    """Store LangGraph checkpoints and writes in a single DynamoDB table."""

    def __init__(self, table_name=None, serde=None, region_name=None):
        super().__init__(serde=serde)
        self.table_name = (table_name or os.environ["CHECKPOINTER_TABLE_NAME"]).strip()
        region = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = self._dynamodb.Table(self.table_name)

    # --- key helpers -------------------------------------------------------

    @staticmethod
    def _pk(thread_id, checkpoint_ns):
        return f"{thread_id}::{checkpoint_ns}"

    @staticmethod
    def _checkpoint_sk(checkpoint_id):
        return f"checkpoint::{checkpoint_id}"

    @staticmethod
    def _write_sk(checkpoint_id, task_id, idx):
        return f"write::{checkpoint_id}::{task_id}::{idx}"

    # --- writes ------------------------------------------------------------

    def put(self, config, checkpoint, metadata, new_versions):
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        LOG.debug(
            "checkpoint put: thread_id=%s ns=%s checkpoint_id=%s",
            thread_id, checkpoint_ns, checkpoint_id,
        )
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")

        checkpoint_type, checkpoint_bytes = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_bytes = self.serde.dumps_typed(metadata)

        item = {
            "pk": self._pk(thread_id, checkpoint_ns),
            "sk": self._checkpoint_sk(checkpoint_id),
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint_id,
            "type": checkpoint_type,
            "checkpoint": Binary(checkpoint_bytes),
            "metadata_type": metadata_type,
            "metadata": Binary(metadata_bytes),
            "expires_at": _expires_at(),
        }
        if parent_checkpoint_id:
            item["parent_checkpoint_id"] = parent_checkpoint_id

        self.table.put_item(Item=item)

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(self, config, writes, task_id, task_path=""):
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        pk = self._pk(thread_id, checkpoint_ns)
        expires_at = _expires_at()

        with self.table.batch_writer() as batch:
            for idx, (channel, value) in enumerate(writes):
                value_type, value_bytes = self.serde.dumps_typed(value)
                batch.put_item(
                    Item={
                        "pk": pk,
                        "sk": self._write_sk(checkpoint_id, task_id, idx),
                        "task_id": task_id,
                        "task_path": task_path,
                        "idx": idx,
                        "channel": channel,
                        "type": value_type,
                        "value": Binary(value_bytes),
                        "expires_at": expires_at,
                    }
                )

    # --- reads -------------------------------------------------------------

    def _load_writes(self, pk, checkpoint_id):
        """Return the pending writes for a checkpoint as (task_id, channel, value)."""
        prefix = f"write::{checkpoint_id}::"
        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(prefix)
        )
        pending = []
        for item in response.get("Items", []):
            value = self.serde.loads_typed((item["type"], _as_bytes(item["value"])))
            pending.append((item["task_id"], item["channel"], value))
        return pending

    def _to_tuple(self, item):
        thread_id = item["thread_id"]
        checkpoint_ns = item.get("checkpoint_ns", "")
        checkpoint_id = item["checkpoint_id"]
        pk = self._pk(thread_id, checkpoint_ns)

        checkpoint = self.serde.loads_typed((item["type"], _as_bytes(item["checkpoint"])))
        metadata = self.serde.loads_typed(
            (item["metadata_type"], _as_bytes(item["metadata"]))
        )

        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

        parent_config = None
        if item.get("parent_checkpoint_id"):
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": item["parent_checkpoint_id"],
                }
            }

        pending_writes = self._load_writes(pk, checkpoint_id)

        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def get_tuple(self, config):
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        pk = self._pk(thread_id, checkpoint_ns)
        checkpoint_id = get_checkpoint_id(config)
        LOG.debug(
            "checkpoint get_tuple: thread_id=%s ns=%s checkpoint_id=%s",
            thread_id, checkpoint_ns, checkpoint_id,
        )

        if checkpoint_id:
            response = self.table.get_item(
                Key={"pk": pk, "sk": self._checkpoint_sk(checkpoint_id)}
            )
            item = response.get("Item")
            if item is None:
                return None
            return self._to_tuple(item)

        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(pk)
            & Key("sk").begins_with("checkpoint::"),
            ScanIndexForward=False,
            Limit=1,
        )
        items = response.get("Items", [])
        if not items:
            return None
        return self._to_tuple(items[0])

    def list(self, config, *, filter=None, before=None, limit=None):
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        pk = self._pk(thread_id, checkpoint_ns)

        query_kwargs = {
            "KeyConditionExpression": Key("pk").eq(pk)
            & Key("sk").begins_with("checkpoint::"),
            "ScanIndexForward": False,
        }

        before_id = get_checkpoint_id(before) if before else None

        yielded = 0
        last_key = None
        while True:
            if last_key is not None:
                query_kwargs["ExclusiveStartKey"] = last_key
            response = self.table.query(**query_kwargs)
            for item in response.get("Items", []):
                if before_id is not None and item["checkpoint_id"] >= before_id:
                    continue
                yield self._to_tuple(item)
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return
