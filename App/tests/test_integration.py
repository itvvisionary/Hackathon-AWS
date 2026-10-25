"""Cross-feature integration test for the order-lookup flow.

Unlike tests/test_agent.py (which swaps in an InMemorySaver and mocks data.*),
this exercises the real pieces wired together against moto-backed DynamoDB:

  * the real ``DynamoDBSaver`` persisting LangGraph state to the checkpointer
    table (so the interrupt survives between the two ``run_agent`` calls),
  * the real ``data.*`` helpers writing the mapping and chat-history tables,
  * the real LangGraph graph and HITL interrupt.

Only the genuinely external boundaries are mocked: the Bedrock LLM (via
``agent._build_llm``) and the order endpoint (``requests.post``). The flow is:
user provides a customer id -> confirm interrupt (no POST) -> resume "yes" ->
POST with exactly {conversation_id, customer_id}, mapping write.
"""

import pytest
from langchain_core.messages import AIMessage

import agent
import data
from checkpointer import DynamoDBSaver


def _ai(text):
    return AIMessage(content=text)


def _ai_list_orders(customer_id):
    return AIMessage(
        content="Looking up your orders now.",
        tool_calls=[
            {
                "name": "list_orders",
                "args": {"customer_id": customer_id, "conversation_id": "ignored"},
                "id": "call-1",
            }
        ],
    )


@pytest.fixture(autouse=True)
def _reset_data_cache():
    """Rebind data.py's cached boto3 handles to the moto mock per test."""
    data._dynamodb = None
    data._tables = {}
    yield
    data._dynamodb = None
    data._tables = {}


def test_end_to_end_interrupt_resume_posts(
    dynamodb, monkeypatch, order_env, mock_requests_post
):
    # Script the Bedrock LLM for the tool-driven flow:
    #   turn 1: ask for the customer id;
    #   turn 2: list_orders (-> confirm interrupt);
    #   after resume: wrap-up message.
    scripted_llm = _ScriptedLLM(
        [
            _ai("Welcome! What's your customer id?"),
            _ai_list_orders("C1001"),
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

    # Turn 1: agent asks for the customer id.
    agent.run_agent(conversation_id, "hi", whatsapp_number=whatsapp_number)
    mock_requests_post.assert_not_called()

    # Turn 2: user supplies the id -> list_orders -> pauses on confirm interrupt.
    reply = agent.run_agent(conversation_id, "my customer id is C1001")
    assert "list the orders" in reply.lower()
    mock_requests_post.assert_not_called()
    assert data.get_mapping(conversation_id) is None

    state = graph.get_state({"configurable": {"thread_id": conversation_id}})
    assert state.next  # thread genuinely paused in the checkpointer table

    # Turn 3: resume "yes" -> the order POST fires and the mapping is persisted.
    agent.run_agent(conversation_id, "yes")

    mock_requests_post.assert_called_once()
    _, kwargs = mock_requests_post.call_args
    # The POST body is EXACTLY these two keys with the state conversation_id.
    assert kwargs["json"] == {
        "conversation_id": conversation_id,
        "customer_id": "C1001",
    }

    mapping = data.get_mapping(conversation_id)
    assert mapping is not None
    assert mapping["workflow_id"] == "237436573"  # RPA id, coerced to string
    assert mapping["customer_id"] == "C1001"
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
