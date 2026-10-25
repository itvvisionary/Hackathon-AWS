"""Offline tests for the LangGraph agent core (agent.py).

Everything external is mocked: the Bedrock LLM (via ``agent._build_llm``),
``requests.post`` to the order endpoint, and the ``data.*`` DynamoDB helpers.
The compiled graph is rebuilt per test with an in-memory checkpointer so the
HITL interrupt/resume behaviour can be exercised without AWS.

There is no authorization step: the user simply provides a customer id and the
LLM calls the ``list_orders`` tool with it. Scenarios:
  1. agent asks for the customer id, then emits list_orders -> the confirm
     interrupt fires with the new prompt and NO HTTP POST happens yet.
  2. resume 'yes' -> requests.post is called once with a body of exactly
     {conversation_id, customer_id} and put_mapping records the row.
  3. resume 'no' -> no POST, no mapping.
  4. ambiguous reply -> re-asks, no POST.
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import agent


def _ai(text):
    return AIMessage(content=text)


def _ai_list_orders(customer_id, call_id="call-1"):
    return AIMessage(
        content="Looking up your orders now.",
        tool_calls=[
            {
                "name": "list_orders",
                # conversation_id here is intentionally bogus: the node must
                # source it from graph state, never from the model's args.
                "args": {"customer_id": customer_id, "conversation_id": "ignored"},
                "id": call_id,
            }
        ],
    )


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
        if not self._responses:
            return AIMessage(content="(no further action)")
        return self._responses.pop(0)


@pytest.fixture
def order_env(monkeypatch):
    monkeypatch.setenv("ORDER_API_ENDPOINT", "https://orders.example.com/start")
    monkeypatch.delenv("ORDER_API_KEY", raising=False)


@pytest.fixture
def mocked(monkeypatch, order_env):
    """Mock data.put_mapping, requests.post, and compile an in-memory graph.

    Returns a harness exposing ``put_mapping``, ``post`` and a ``set_llm``
    helper so each test scripts the LLM responses it needs.
    """
    put_mapping = MagicMock()
    monkeypatch.setattr(agent.data, "put_mapping", put_mapping)

    response = MagicMock()
    # Real RPA response shape: the workflow id is the numeric ``id`` field.
    response.json.return_value = {
        "key": "687250db-0ce4-4be4-9f37-f7592962492e",
        "state": "Pending",
        "id": 237436573,
    }
    response.raise_for_status.return_value = None
    post = MagicMock(return_value=response)
    monkeypatch.setattr(agent.requests, "post", post)

    graph = agent.build_graph(checkpointer=InMemorySaver())
    monkeypatch.setattr(agent, "compiled_graph", graph)

    class Harness:
        pass

    harness = Harness()
    harness.put_mapping = put_mapping
    harness.post = post

    def set_llm(*responses):
        # Build ONE FakeLLM and return it on every _build_llm() call so the
        # scripted responses advance across the multiple LLM invocations a
        # single run_agent turn can make.
        llm = FakeLLM(responses)
        monkeypatch.setattr(agent, "_build_llm", lambda: llm)

    harness.set_llm = set_llm
    return harness


def _state(conversation_id):
    return agent.compiled_graph.get_state(
        {"configurable": {"thread_id": conversation_id}}
    )


# --- Scenario (a): interrupt fires before any POST -------------------------


def test_list_orders_interrupts_without_post(mocked):
    mocked.set_llm(
        _ai("Welcome! What's your customer id?"),  # turn 1: ask for the id
        _ai_list_orders("C1001"),  # turn 2: emit the lookup
    )

    # Turn 1: agent asks for the customer id.
    agent.run_agent("conv-1", "hi")
    # Turn 2: user supplies the id; agent emits list_orders -> confirm interrupt.
    reply = agent.run_agent("conv-1", "my customer id is C1001")

    assert _state("conv-1").next  # paused on the confirm interrupt
    assert "list the orders" in reply.lower()
    assert "C1001" in reply  # the customer id is shown
    assert "yes or no" in reply.lower()
    mocked.post.assert_not_called()
    mocked.put_mapping.assert_not_called()


# --- Scenario (b): resume 'yes' triggers POST + mapping --------------------


def test_resume_yes_triggers_post_and_mapping(mocked):
    mocked.set_llm(
        _ai("What's your customer id?"),
        _ai_list_orders("C1001"),
        _ai("All set!"),
    )

    agent.run_agent("conv-4", "hi", whatsapp_number="+15551230000")
    agent.run_agent("conv-4", "it's C1001")
    mocked.post.assert_not_called()  # paused on interrupt, not run yet

    agent.run_agent("conv-4", "yes")

    mocked.post.assert_called_once()
    _, kwargs = mocked.post.call_args
    # The body MUST be EXACTLY these two keys, with the state conversation_id.
    assert kwargs["json"] == {"conversation_id": "conv-4", "customer_id": "C1001"}
    assert "Authorization" not in kwargs["headers"]

    # Workflow id is the RPA ``id`` field, coerced to a string. A whatsapp_number
    # was supplied, so the channel is recorded as "whatsapp".
    mocked.put_mapping.assert_called_once_with(
        "conv-4", "237436573", "C1001", "+15551230000", "whatsapp"
    )


def test_resume_yes_api_channel_when_no_whatsapp_number(mocked):
    # No whatsapp_number (UI/API path) -> channel recorded as "api".
    mocked.set_llm(
        _ai("What's your customer id?"),
        _ai_list_orders("C1001"),
        _ai("All set!"),
    )

    agent.run_agent("conv-api", "hi")  # no whatsapp_number
    agent.run_agent("conv-api", "C1001")
    agent.run_agent("conv-api", "yes")

    mocked.put_mapping.assert_called_once_with(
        "conv-api", "237436573", "C1001", "", "api"
    )


# --- Scenario (c): resume 'no' cancels, no POST ----------------------------


def test_resume_no_does_not_post(mocked):
    mocked.set_llm(
        _ai("What's your customer id?"),
        _ai_list_orders("C1001"),
    )

    agent.run_agent("conv-5", "hi")
    agent.run_agent("conv-5", "C1001")
    agent.run_agent("conv-5", "no")

    mocked.post.assert_not_called()
    mocked.put_mapping.assert_not_called()


# --- Scenario (d): ambiguous reply re-asks, then 'yes' posts ---------------


def test_ambiguous_confirm_reasks_then_yes_posts(mocked):
    mocked.set_llm(
        _ai("What's your customer id?"),
        _ai_list_orders("C1001"),
        _ai("All set!"),
    )

    agent.run_agent("conv-5b", "hi")
    agent.run_agent("conv-5b", "C1001")

    # An ambiguous reply should NOT cancel and NOT run — it re-asks.
    reply = agent.run_agent("conv-5b", "hmm maybe")
    assert _state("conv-5b").next  # still paused on the confirm interrupt
    assert "yes" in reply.lower() and "no" in reply.lower()
    mocked.post.assert_not_called()

    # A clear 'yes' now runs the lookup.
    agent.run_agent("conv-5b", "yes")
    mocked.post.assert_called_once()


# --- Workflow id parsed from the RPA "id" field ----------------------------


def test_workflow_id_parsed_from_id_field(mocked):
    mocked.set_llm(
        _ai("What's your customer id?"),
        _ai_list_orders("C1001"),
        _ai("All set!"),
    )

    agent.run_agent("conv-id", "hi")
    agent.run_agent("conv-id", "C1001")
    agent.run_agent("conv-id", "yes")

    # The RPA response ``id`` (237436573) is coerced to the string "237436573".
    # put_mapping is called positionally (conversation_id, workflow_id,
    # customer_id, whatsapp_number, channel).
    _, workflow_id, _, _, _ = mocked.put_mapping.call_args.args
    assert workflow_id == "237436573"


# --- Extra: ORDER_API_KEY adds the bearer header ---------------------------


def test_order_api_key_sets_authorization_header(mocked, monkeypatch):
    monkeypatch.setenv("ORDER_API_KEY", "secret-key")
    mocked.set_llm(
        _ai("What's your customer id?"),
        _ai_list_orders("C1001"),
        _ai("All set!"),
    )

    agent.run_agent("conv-7", "hi")
    agent.run_agent("conv-7", "C1001")
    agent.run_agent("conv-7", "yes")

    _, kwargs = mocked.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
