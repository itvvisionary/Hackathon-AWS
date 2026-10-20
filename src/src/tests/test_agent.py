"""Offline tests for the LangGraph agent core (agent.py).

Everything external is mocked: the Bedrock LLM (via ``agent._build_llm``),
``requests.post`` to the order endpoint, and the ``data.*`` DynamoDB helpers.
The compiled graph is rebuilt per test with an in-memory checkpointer so the
HITL interrupt/resume behaviour can be exercised without AWS.

The five scenarios from FEAT-002:
  1. unauthorized id -> reply refuses and re-asks, no order
  2. authorized id -> proceeds to collect params
  3. all params collected -> interrupt fires, NO HTTP POST yet
  4. resume with 'yes' -> requests.post IS called (body has conversation_id)
     and put_mapping is called with (conversation_id, workflow_id, user_id,
     whatsapp_number)
  5. resume with 'no' -> no POST
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import agent


ORDER_ARGS = {
    "user_name": "Ada Lovelace",
    "user_email": "ada@example.com",
    "user_address": "1 Analytical Engine Way",
    "products": "Widgets",
    "number_of_products": 3,
    "conversation_id": "llm-supplied-will-be-ignored",
}


class FakeLLM:
    """Stand-in for the tool-bound ChatBedrockConverse.

    ``invoke`` pops and returns the next scripted AIMessage. ``bind_tools`` is a
    no-op returning self so the production binding call is harmless.
    """

    def __init__(self, responses):
        self._responses = list(responses)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return self._responses.pop(0)


def _ai(text):
    return AIMessage(content=text)


def _ai_create_order():
    return AIMessage(
        content="Placing your order now.",
        tool_calls=[
            {"name": "create_order", "args": dict(ORDER_ARGS), "id": "call-1"}
        ],
    )


@pytest.fixture
def order_env(monkeypatch):
    monkeypatch.setenv("ORDER_API_ENDPOINT", "https://orders.example.com/start")
    monkeypatch.delenv("ORDER_API_KEY", raising=False)


@pytest.fixture
def mocked(monkeypatch, order_env):
    """Mock data.*, requests.post, and compile an in-memory graph.

    Returns an object exposing ``is_authorized``, ``put_mapping``, ``post`` and
    a ``set_llm`` helper so each test scripts the LLM responses it needs.
    """

    is_authorized = MagicMock(return_value=True)
    put_mapping = MagicMock()
    monkeypatch.setattr(agent.data, "is_authorized_user", is_authorized)
    monkeypatch.setattr(agent.data, "put_mapping", put_mapping)

    response = MagicMock()
    response.json.return_value = {"workflow_id": "wf-777"}
    response.raise_for_status.return_value = None
    post = MagicMock(return_value=response)
    monkeypatch.setattr(agent.requests, "post", post)

    graph = agent.build_graph(checkpointer=InMemorySaver())
    monkeypatch.setattr(agent, "compiled_graph", graph)

    class Harness:
        pass

    harness = Harness()
    harness.is_authorized = is_authorized
    harness.put_mapping = put_mapping
    harness.post = post

    def set_llm(*responses):
        monkeypatch.setattr(agent, "_build_llm", lambda: FakeLLM(responses))

    harness.set_llm = set_llm
    return harness


# --- Scenario 1: unauthorized ---------------------------------------------


def test_unauthorized_id_refuses_and_no_order(mocked):
    mocked.is_authorized.return_value = False
    mocked.set_llm()  # LLM should never be reached

    reply = agent.run_agent("conv-1", "bad-id")

    assert "verify" in reply.lower() or "id" in reply.lower()
    mocked.post.assert_not_called()
    mocked.put_mapping.assert_not_called()


# --- Scenario 2: authorized -> collect params ------------------------------


def test_authorized_id_proceeds_to_collect_params(mocked):
    mocked.is_authorized.return_value = True
    mocked.set_llm(_ai("Great, what's your name and email to start the order?"))

    reply = agent.run_agent("conv-2", "u-123")

    mocked.is_authorized.assert_called_once_with("u-123")
    assert reply  # a non-empty AI prompt to collect order details
    mocked.post.assert_not_called()


# --- Scenario 3: interrupt fires before any POST ---------------------------


def test_all_params_collected_interrupts_without_post(mocked):
    mocked.is_authorized.return_value = True
    mocked.set_llm(_ai_create_order())

    reply = agent.run_agent("conv-3", "u-123", whatsapp_number="+15551230000")

    # Paused on the confirm interrupt; no HTTP POST yet.
    state = agent.compiled_graph.get_state(
        {"configurable": {"thread_id": "conv-3"}}
    )
    assert state.next  # thread is paused awaiting resume
    assert "confirm" in reply.lower()
    mocked.post.assert_not_called()
    mocked.put_mapping.assert_not_called()


# --- Scenario 4: resume 'yes' triggers POST + mapping ----------------------


def test_resume_yes_triggers_post_and_mapping(mocked):
    mocked.is_authorized.return_value = True
    # First turn emits the create_order call (-> interrupt); after resume the
    # agent node runs once more and wraps up without further tool calls.
    mocked.set_llm(_ai_create_order(), _ai("All done!"))

    agent.run_agent("conv-4", "u-123", whatsapp_number="+15551230000")
    mocked.post.assert_not_called()  # paused, not run yet

    agent.run_agent("conv-4", "yes")

    mocked.post.assert_called_once()
    _, kwargs = mocked.post.call_args
    body = kwargs["json"]
    assert body["conversation_id"] == "conv-4"
    assert "Authorization" not in kwargs["headers"]

    mocked.put_mapping.assert_called_once_with(
        "conv-4", "wf-777", "u-123", "+15551230000"
    )


# --- Scenario 5: resume 'no' cancels, no POST ------------------------------


def test_resume_no_does_not_post(mocked):
    mocked.is_authorized.return_value = True
    mocked.set_llm(_ai_create_order())

    agent.run_agent("conv-5", "u-123")
    agent.run_agent("conv-5", "no")

    mocked.post.assert_not_called()
    mocked.put_mapping.assert_not_called()


# --- Extra: ORDER_API_KEY adds the bearer header ---------------------------


def test_order_api_key_sets_authorization_header(mocked, monkeypatch):
    monkeypatch.setenv("ORDER_API_KEY", "secret-key")
    mocked.is_authorized.return_value = True
    mocked.set_llm(_ai_create_order(), _ai("All done!"))

    agent.run_agent("conv-6", "u-123")
    agent.run_agent("conv-6", "yes")

    _, kwargs = mocked.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
