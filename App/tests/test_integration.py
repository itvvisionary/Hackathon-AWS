"""Cross-feature integration test for the ordering flow.

Unlike tests/test_agent.py (which swaps in an InMemorySaver and mocks data.*),
this exercises the real pieces wired together against moto-backed DynamoDB:

  * the real ``DynamoDBSaver`` persisting LangGraph state to the checkpointer
    table (so the interrupt survives between the two ``run_agent`` calls),
  * the real ``data.*`` helpers reading the users table and writing the mapping
    and chat-history tables,
  * the real LangGraph graph and HITL interrupt.

Only the genuinely external boundaries are mocked: the Bedrock LLM (via
``agent._build_llm``) and the order endpoint (``requests.post``). The flow is:
authorized id -> collect + confirm interrupt (no POST) -> resume "yes" -> POST,
mapping write, and recorded chat turns.
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

import agent
import data
from checkpointer import DynamoDBSaver
from conftest import USERS_TABLE_NAME


ORDER_ARGS = {
    "user_name": "Ada Lovelace",
    "user_email": "ada@example.com",
    "user_address": "1 Analytical Engine Way",
    "products": "Widgets",
    "number_of_products": 3,
    "conversation_id": "llm-supplied-ignored",
}


def _ai_verify(user_id):
    return AIMessage(
        content="Checking that ID.",
        tool_calls=[{"name": "verify_user", "args": {"user_id": user_id}, "id": "verify-1"}],
    )


def _ai_create_order():
    return AIMessage(
        content="Placing your order now.",
        tool_calls=[{"name": "create_order", "args": dict(ORDER_ARGS), "id": "call-1"}],
    )


@pytest.fixture(autouse=True)
def _reset_data_cache():
    """Rebind data.py's cached boto3 handles to the moto mock per test."""
    data._dynamodb = None
    data._tables = {}
    yield
    data._dynamodb = None
    data._tables = {}


def test_end_to_end_authorized_interrupt_resume_posts(
    dynamodb, monkeypatch, order_env, mock_requests_post
):
    # Seed an authorized user in the real (moto) users table.
    dynamodb.Table(USERS_TABLE_NAME).put_item(Item={"user_id": "U1001"})

    # Script the Bedrock LLM for the tool-driven flow:
    #   turn 1: verify_user (-> authorized), then collect prompt;
    #   turn 2: create_order (-> confirm interrupt);
    #   after resume: wrap-up message.
    # Build ONE scripted LLM reused on every _build_llm() call so responses
    # advance across the tool loops (a fresh instance per call would replay
    # verify_user forever and never terminate).
    scripted_llm = _ScriptedLLM(
        [
            _ai_verify("U1001"),
            AIMessage(content="You're verified. Share your order details."),
            _ai_create_order(),
            AIMessage(content="All done!"),
        ]
    )
    monkeypatch.setattr(agent, "_build_llm", lambda: scripted_llm)

    # Use the real DynamoDB checkpointer so the paused thread survives the gap
    # between the two run_agent calls.
    graph = agent.build_graph(checkpointer=DynamoDBSaver())
    monkeypatch.setattr(agent, "compiled_graph", graph)

    conversation_id = "conv-int-1"
    whatsapp_number = "+15551230000"

    # Turn 1: natural-language id -> verify_user authorizes, agent asks for details.
    agent.run_agent(conversation_id, "hi, my id is U1001", whatsapp_number=whatsapp_number)
    mock_requests_post.assert_not_called()

    # Turn 2: user supplies details -> create_order -> pauses on confirm interrupt.
    reply = agent.run_agent(conversation_id, "here are my order details")
    assert "confirm" in reply.lower()
    mock_requests_post.assert_not_called()
    assert data.get_mapping(conversation_id) is None

    state = graph.get_state({"configurable": {"thread_id": conversation_id}})
    assert state.next  # thread genuinely paused in the checkpointer table

    # Turn 3: resume "yes" -> the order POST fires and the mapping is persisted.
    agent.run_agent(conversation_id, "yes")

    mock_requests_post.assert_called_once()
    _, kwargs = mock_requests_post.call_args
    assert kwargs["json"]["conversation_id"] == conversation_id

    mapping = data.get_mapping(conversation_id)
    assert mapping is not None
    assert mapping["workflow_id"] == "237436573"  # RPA id, coerced to string
    assert mapping["user_id"] == "U1001"
    assert mapping["whatsapp_number"] == whatsapp_number
    assert mapping["channel"] == "whatsapp"  # came in with a whatsapp_number


class _ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        if not self._responses:
            return AIMessage(content="(no further action)")
        return self._responses.pop(0)


@pytest.fixture
def order_env(monkeypatch):
    monkeypatch.setenv("ORDER_API_ENDPOINT", "https://orders.example.com/start")
    monkeypatch.delenv("ORDER_API_KEY", raising=False)
