"""Shared offline test setup.

Sets deterministic, non-secret environment variables (table names, region, dummy
AWS credentials) before the modules under test import boto3, and exposes moto
fixtures that create the four DynamoDB tables so the whole suite runs with no
network and no real AWS account.
"""

import os
import sys
from unittest.mock import MagicMock

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


class ScriptedLLM:
    """Offline stand-in for the tool-bound ``ChatBedrockConverse``.

    ``invoke`` returns the next scripted ``AIMessage`` so tests never touch
    Bedrock. ``bind_tools`` is a no-op returning self so the production binding
    call in ``agent._build_llm`` is harmless.
    """

    def __init__(self, responses):
        self._responses = list(responses)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return self._responses.pop(0)


@pytest.fixture
def scripted_llm():
    """Return a factory that builds a :class:`ScriptedLLM` from AIMessages."""

    def _make(*responses):
        return ScriptedLLM(responses)

    return _make


@pytest.fixture
def order_response():
    """A requests.Response-like mock for the order endpoint (workflow_id)."""

    response = MagicMock()
    response.json.return_value = {"workflow_id": "wf-777"}
    response.raise_for_status.return_value = None
    return response


@pytest.fixture
def mock_requests_post(monkeypatch, order_response):
    """Patch requests.post on the agent module; return the mock."""
    import agent

    post = MagicMock(return_value=order_response)
    monkeypatch.setattr(agent.requests, "post", post)
    return post


@pytest.fixture
def mock_socialmessaging(monkeypatch):
    """Patch the outbound WhatsApp send so no socialmessaging call is made."""
    import whatsapp

    send = MagicMock()
    monkeypatch.setattr(whatsapp, "send_whatsapp_message", send)
    return send
