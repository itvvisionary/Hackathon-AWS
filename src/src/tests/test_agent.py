"""Offline tests for the LangGraph agent core (agent.py).

Everything external is mocked: the Bedrock LLM (via ``agent._build_llm``),
``requests.post`` to the order endpoint, and the ``data.*`` DynamoDB helpers.
The compiled graph is rebuilt per test with an in-memory checkpointer so the
HITL interrupt/resume behaviour can be exercised without AWS.

Authorization is now tool-driven: the LLM extracts the id from the user's
message and calls the ``verify_user`` tool, which returns authorized true/false.
Only after an authorized=true result may any other tool run. Scenarios:
  1. unauthorized id -> verify_user returns false, order tools stay blocked
  2. authorized id   -> verify_user returns true, agent proceeds to collect
  3. all params collected -> interrupt fires, NO HTTP POST yet
  4. resume 'yes'    -> requests.post IS called (body has conversation_id) and
     put_mapping is called with (conversation_id, workflow_id, user_id, number)
  5. resume 'no'     -> no POST
  6. create_order before verification is refused (gate), no POST
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

    The exact number of times the graph calls the LLM per ``run_agent`` turn
    depends on tool loops (verify_user loops back to the agent), so tests script
    the full ordered list of responses across the whole scenario; each invoke
    consumes the next one.
    """

    def __init__(self, responses):
        self._responses = list(responses)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        if not self._responses:
            # Nothing more scripted: return an inert assistant message so the
            # graph ends this turn instead of raising IndexError.
            return AIMessage(content="(no further action)")
        return self._responses.pop(0)


def _ai(text):
    return AIMessage(content=text)


def _ai_verify(user_id, call_id="verify-1"):
    return AIMessage(
        content="Let me check that ID.",
        tool_calls=[{"name": "verify_user", "args": {"user_id": user_id}, "id": call_id}],
    )


def _ai_create_order(call_id="call-1"):
    return AIMessage(
        content="Placing your order now.",
        tool_calls=[{"name": "create_order", "args": dict(ORDER_ARGS), "id": call_id}],
    )


@pytest.fixture
def order_env(monkeypatch):
    monkeypatch.setenv("ORDER_API_ENDPOINT", "https://orders.example.com/start")
    monkeypatch.delenv("ORDER_API_KEY", raising=False)


@pytest.fixture
def mocked(monkeypatch, order_env):
    """Mock data.*, requests.post, and compile an in-memory graph.

    Returns a harness exposing ``is_authorized``, ``put_mapping``, ``post`` and
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
        # Build ONE FakeLLM and return it on every _build_llm() call so the
        # scripted responses advance across the multiple LLM invocations a
        # single run_agent turn can make (the agent node re-runs after each
        # tool loop). A fresh FakeLLM per call would always replay response #1.
        llm = FakeLLM(responses)
        monkeypatch.setattr(agent, "_build_llm", lambda: llm)

    harness.set_llm = set_llm
    return harness


def _state(conversation_id):
    return agent.compiled_graph.get_state(
        {"configurable": {"thread_id": conversation_id}}
    )


# --- Scenario 1: unauthorized ---------------------------------------------


def test_unauthorized_id_does_not_authorize(mocked):
    mocked.is_authorized.return_value = False
    # LLM extracts the id and calls verify_user; the tool returns false.
    mocked.set_llm(
        _ai_verify("U-bad"),
        _ai("I couldn't verify that ID. Please double-check and send it again."),
    )

    reply = agent.run_agent("conv-1", "ah, my id is U-bad")

    mocked.is_authorized.assert_called_once_with("U-bad")
    assert _state("conv-1").values.get("authorized") is not True
    assert reply
    mocked.post.assert_not_called()
    mocked.put_mapping.assert_not_called()


# --- Scenario 2: authorized -> collect params ------------------------------


def test_authorized_id_proceeds_to_collect_params(mocked):
    mocked.is_authorized.return_value = True
    # verify_user call (-> true), then the agent asks for order details.
    mocked.set_llm(
        _ai_verify("U1001"),
        _ai("Great, you're verified. What's your name and email to start?"),
    )

    reply = agent.run_agent("conv-2", "my id is U1001")

    mocked.is_authorized.assert_called_once_with("U1001")
    assert _state("conv-2").values.get("authorized") is True
    assert _state("conv-2").values.get("user_id") == "U1001"
    assert reply
    mocked.post.assert_not_called()


# --- Scenario 3: interrupt fires before any POST ---------------------------


def test_all_params_collected_interrupts_without_post(mocked):
    mocked.is_authorized.return_value = True
    mocked.set_llm(
        _ai_verify("U1001"),
        _ai("Thanks! I have your details."),  # after verify, collect
        _ai_create_order(),  # once params gathered, request the order
    )

    # Turn 1: verify + authorize
    agent.run_agent("conv-3", "id U1001", whatsapp_number="+15551230000")
    # Turn 2: user provides params; agent emits create_order -> interrupt
    reply = agent.run_agent("conv-3", "here are my details ...")

    assert _state("conv-3").next  # paused on the confirm interrupt
    assert "confirm" in reply.lower()
    mocked.post.assert_not_called()
    mocked.put_mapping.assert_not_called()


# --- Scenario 4: resume 'yes' triggers POST + mapping ----------------------


def test_resume_yes_triggers_post_and_mapping(mocked):
    mocked.is_authorized.return_value = True
    mocked.set_llm(
        _ai_verify("U1001"),
        _ai("Thanks, verified."),
        _ai_create_order(),
        _ai("All done!"),
    )

    agent.run_agent("conv-4", "my id is U1001", whatsapp_number="+15551230000")
    agent.run_agent("conv-4", "my details are ...")
    mocked.post.assert_not_called()  # paused on interrupt, not run yet

    agent.run_agent("conv-4", "yes")

    mocked.post.assert_called_once()
    _, kwargs = mocked.post.call_args
    body = kwargs["json"]
    assert body["conversation_id"] == "conv-4"
    assert "Authorization" not in kwargs["headers"]

    mocked.put_mapping.assert_called_once_with(
        "conv-4", "wf-777", "U1001", "+15551230000"
    )


# --- Scenario 5: resume 'no' cancels, no POST ------------------------------


def test_resume_no_does_not_post(mocked):
    mocked.is_authorized.return_value = True
    mocked.set_llm(
        _ai_verify("U1001"),
        _ai("Verified."),
        _ai_create_order(),
    )

    agent.run_agent("conv-5", "id: U1001")
    agent.run_agent("conv-5", "my details ...")
    agent.run_agent("conv-5", "no")

    mocked.post.assert_not_called()
    mocked.put_mapping.assert_not_called()


# --- Scenario 6: create_order before verification is gated -----------------


def test_create_order_before_verification_is_refused(mocked):
    mocked.is_authorized.return_value = True  # would authorize IF verify ran
    # The model (mis)behaves: it tries to order before verifying.
    mocked.set_llm(
        _ai_create_order(),
        _ai("You need to verify first - what's your user ID?"),
    )

    agent.run_agent("conv-6", "just place an order for 3 widgets")

    # The gate blocked create_order: no POST, no mapping, not authorized.
    assert _state("conv-6").values.get("authorized") is not True
    mocked.post.assert_not_called()
    mocked.put_mapping.assert_not_called()


# --- Extra: ORDER_API_KEY adds the bearer header ---------------------------


def test_order_api_key_sets_authorization_header(mocked, monkeypatch):
    monkeypatch.setenv("ORDER_API_KEY", "secret-key")
    mocked.is_authorized.return_value = True
    mocked.set_llm(
        _ai_verify("U1001"),
        _ai("Verified."),
        _ai_create_order(),
        _ai("All done!"),
    )

    agent.run_agent("conv-7", "my id is U1001")
    agent.run_agent("conv-7", "details ...")
    agent.run_agent("conv-7", "yes")

    _, kwargs = mocked.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
