"""Shared offline test setup.

Sets deterministic, non-secret environment variables (table names, region, dummy
AWS credentials) before the modules under test import boto3, and exposes moto
fixtures that create the four DynamoDB tables so the whole suite runs with no
network and no real AWS account.
"""

import os
import sys

import boto3
import pytest
from moto import mock_aws

# Make the worktree modules importable regardless of the pytest invocation cwd.
sys.path.insert(0, os.path.dirname(__file__))

USERS_TABLE_NAME = "test-users"
CHAT_HISTORY_TABLE_NAME = "test-chat-history"
CHECKPOINTER_TABLE_NAME = "test-checkpointer"
MAPPING_TABLE_NAME = "test-mapping"

# Modules under test (agent.py) build their LangGraph graph at import time, and
# the DynamoDBSaver reads CHECKPOINTER_TABLE_NAME eagerly. Pytest imports the
# test modules during collection, before any fixture runs, so seed the table
# names (and region) at conftest import time too. The autouse ``_env`` fixture
# below still re-applies these per test via monkeypatch for isolation.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("USERS_TABLE_NAME", USERS_TABLE_NAME)
os.environ.setdefault("CHAT_HISTORY_TABLE_NAME", CHAT_HISTORY_TABLE_NAME)
os.environ.setdefault("CHECKPOINTER_TABLE_NAME", CHECKPOINTER_TABLE_NAME)
os.environ.setdefault("MAPPING_TABLE_NAME", MAPPING_TABLE_NAME)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Populate env vars (table names, region, dummy creds) for every test."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("USERS_TABLE_NAME", USERS_TABLE_NAME)
    monkeypatch.setenv("CHAT_HISTORY_TABLE_NAME", CHAT_HISTORY_TABLE_NAME)
    monkeypatch.setenv("CHECKPOINTER_TABLE_NAME", CHECKPOINTER_TABLE_NAME)
    monkeypatch.setenv("MAPPING_TABLE_NAME", MAPPING_TABLE_NAME)


@pytest.fixture
def dynamodb(_env):
    """Start moto and create the four tables; yield the dynamodb resource."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")

        resource.create_table(
            TableName=USERS_TABLE_NAME,
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        resource.create_table(
            TableName=CHAT_HISTORY_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "conversation_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "conversation_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        resource.create_table(
            TableName=CHECKPOINTER_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        resource.create_table(
            TableName=MAPPING_TABLE_NAME,
            KeySchema=[{"AttributeName": "conversation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "conversation_id", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield resource
