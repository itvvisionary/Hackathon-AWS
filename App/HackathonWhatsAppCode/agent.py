"""LangGraph agent core: state, tools, LLM, HITL interrupt, and run_agent.

The graph implements the order-lookup flow both triggers share:

1. Welcome + ask the user for their customer id. The id is NOT verified against
   any table — the user simply provides it and the LLM calls the
   ``list_orders`` tool with it.
2. Before the ``list_orders`` tool runs, pause with ``interrupt(...)`` so the
   user can confirm they want to list the orders for that customer id. Only an
   explicit affirmative resume value lets the lookup proceed; anything else
   cancels, and an ambiguous reply re-asks.
3. ``list_orders`` POSTs to the RPA/SAP order endpoint (body contains exactly
   ``conversation_id`` and ``customer_id``), parses the workflow id from the
   response ``id`` field, and records the conversation->workflow mapping via
   ``data.put_mapping``.

There is no authorization step: all tools may run without verification. The
``conversation_id`` and ``whatsapp_number`` are taken from graph state (never
from LLM-supplied tool arguments) when the lookup is started. Every setting
comes from the environment; nothing is hardcoded.
"""

import datetime
import json
import os
from typing import Annotated, TypedDict

import requests
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

import data
from checkpointer import DynamoDBSaver
from logging_setup import get_logger, log_io

LOG = get_logger(__name__)

DEFAULT_MODEL_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
ORDER_API_TIMEOUT = 30

SYSTEM_PROMPT = (
    "You are a friendly orders assistant. Start every new conversation with a "
    "short welcome and ask the user for their customer id so you can look up "
    "their orders. The user may give the id conversationally (for example 'my "
    "customer id is C1001' or 'it's C1001') - extract just the id token from "
    "their message. Do NOT verify the id against anything; simply take what the "
    "user gives you.\n"
    "\n"
    "Once you have the customer id, call the list_orders tool with it. A "
    "confirmation step will then ask the user to answer yes or no before the "
    "lookup actually runs.\n"
    )


# --- Graph state -----------------------------------------------------------


class AgentState(TypedDict):
    """Mutable state threaded through the graph for one conversation."""

    messages: Annotated[list, add_messages]
    conversation_id: str
    whatsapp_number: str
    # Set when a confirm reply was neither yes nor no, so the graph loops back
    # into the confirm node to re-ask for a clean yes/no.
    reconfirm: bool


# --- Tools -----------------------------------------------------------------


@tool
def get_current_time() -> str:
    """Return the current UTC time in ISO 8601 and a human-readable form."""
    now = datetime.datetime.now(datetime.timezone.utc)
    iso = now.isoformat()
    human = now.strftime("%A, %d %B %Y at %H:%M:%S UTC")
    return f"Current time: {iso} ({human})"


@tool
def list_orders(customer_id: str, conversation_id: str) -> dict:
    """List the orders for a customer id via the RPA/SAP order endpoint.

    The ``customer_id`` is whatever the user provided (never verified). POSTs to
    the configured order API, reads the workflow id from the response ``id``
    field, and records the conversation->workflow mapping. ``conversation_id``
    is injected from graph state, not from the model's arguments.
    """
    return list_orders_request(
        customer_id=customer_id,
        conversation_id=conversation_id,
        whatsapp_number="",
        channel="api",
    )


@log_io()
def list_orders_request(
    *,
    customer_id,
    conversation_id,
    whatsapp_number,
    channel,
):
    """Perform the order-lookup POST + mapping write using state-sourced ids.

    Separated from the ``list_orders`` tool so the executing node can supply
    ``conversation_id``/``whatsapp_number``/``channel`` from graph state rather
    than trusting the model. The POST body contains exactly ``conversation_id``
    and ``customer_id``. ``channel`` is ``"whatsapp"`` or ``"api"`` and is
    stored in the mapping so the callback can route the completion the same way.
    The workflow id is taken from the RPA response ``id`` field (a numeric id,
    coerced to a string). Returns a dict with ``workflow_id`` and a user-facing
    confirmation.
    """
    endpoint = os.environ["ORDER_API_ENDPOINT"]
    body = {
        "conversation_id": conversation_id,
        "customer_id": customer_id,
    }
    headers = {"Content-Type": "application/json"}
    # Always attach a bearer Authorization header when an API key is configured.
    # Accept the token with or without an explicit "Bearer " prefix so the env
    # value can be either "<token>" or "Bearer <token>" without double-prefixing.
    api_key = (os.environ.get("ORDER_API_KEY") or "").strip()
    if api_key:
        if api_key.lower().startswith("bearer "):
            headers["Authorization"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    LOG.debug("order POST input: endpoint=%s body=%r auth=%s", endpoint, body,
              "yes" if "Authorization" in headers else "no")
    response = requests.post(
        endpoint, json=body, headers=headers, timeout=ORDER_API_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    LOG.debug("order POST output: status=%s payload=%r",
              getattr(response, "status_code", "?"), payload)

    # The RPA response identifies the workflow by its ``id`` field, e.g.
    #   {"key": "...", "state": "Pending", "id": 237436573, ...}
    # Fall back to ``workflow_id`` for compatibility with other RPA shapes.
    raw_id = payload.get("id")
    if raw_id is None:
        raw_id = payload.get("workflow_id")
    # Store/compare the workflow id as a string (DynamoDB key + callback match).
    workflow_id = str(raw_id) if raw_id is not None else None

    data.put_mapping(conversation_id, workflow_id, customer_id, whatsapp_number, channel)

    return {
        "workflow_id": workflow_id,
        "message": (
            f"I've started the lookup for customer {customer_id}. Your workflow "
            f"ID is {workflow_id}. I'll share the orders here once they arrive."
        ),
    }


TOOLS = [get_current_time, list_orders]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


# --- LLM -------------------------------------------------------------------


def _build_llm():
    model_id = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    region = os.environ.get("AWS_REGION", "us-east-1")
    return ChatBedrockConverse(model=model_id, region_name=region).bind_tools(TOOLS)


# --- Nodes -----------------------------------------------------------------


@log_io()
def agent_node(state: AgentState) -> dict:
    """Call the tool-bound LLM with the system prompt prepended."""
    llm = _build_llm()
    messages = [("system", SYSTEM_PROMPT), *state["messages"]]
    response = llm.invoke(messages)
    return {"messages": [response]}


def _format_order_summary(params):
    """Build the human-readable confirm prompt shown at the confirm step."""
    customer_id = params.get("customer_id", "")
    return (
        "Do you want to list the orders for that customer id? "
        f"(customer id: {customer_id}) (enter: yes or no)"
    )


@log_io()
def confirm_node(state: AgentState) -> dict:
    """Pause before list_orders so the user can confirm the lookup.

    Shows the customer id and asks the user to answer **yes or no**.
    ``interrupt`` pauses until the user replies:
      * an affirmative (yes/ok/confirm/...) proceeds to the lookup,
      * a clear negative (no/cancel/stop/...) cancels,
      * anything ambiguous re-prompts (loops the interrupt) instead of
        silently cancelling, so a stray reply never drops the lookup.
    """
    tool_call = _pending_list_orders_call(state["messages"])
    params = dict(tool_call["args"]) if tool_call else {}
    summary = _format_order_summary(params)

    # One interrupt per entry into the node. On an ambiguous reply the node
    # returns ``reconfirm=True`` and the graph routes straight back here, which
    # raises a brand-new interrupt so the thread pauses again for a clean yes/no
    # (instead of silently cancelling or placing the order).
    if state.get("reconfirm"):
        prompt = (
            "Sorry, I didn't catch that - please answer 'yes' to list the "
            "orders or 'no' to cancel.\n\n" + summary
        )
    else:
        prompt = summary

    decision = interrupt(
        {
            "type": "confirm_list_orders",
            "message": prompt,
            "parameters": params,
        }
    )

    if _is_affirmative(decision):
        return {"reconfirm": False}

    if _is_negative(decision):
        return {
            "reconfirm": False,
            "messages": [
                ToolMessage(
                    content="Order lookup cancelled by the user.",
                    tool_call_id=tool_call["id"] if tool_call else "cancelled",
                ),
                AIMessage(
                    content=(
                        "No problem, I've cancelled that lookup. Let me know if "
                        "you'd like to try again with a different customer id."
                    )
                ),
            ],
        }

    # Ambiguous: flag for a re-ask and route back into confirm.
    return {"reconfirm": True}


@log_io()
def tools_node(state: AgentState) -> dict:
    """Execute the tools requested by the last AI message.

    No authorization step: every tool may run. For ``list_orders`` the
    ``conversation_id``/``whatsapp_number`` come from graph state, not the
    model's tool arguments, and ``channel`` is derived from whether a WhatsApp
    sender number is present.
    """
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    outputs = []

    for call in tool_calls:
        name = call["name"]
        args = dict(call.get("args", {}))

        if name == "list_orders":
            wa_number = state.get("whatsapp_number", "") or ""
            # Channel is derived from how the conversation reached us: a WhatsApp
            # sender number means the SQS/WhatsApp path; its absence means the
            # HTTP /ask (UI) path.
            channel = "whatsapp" if wa_number else "api"
            result = list_orders_request(
                customer_id=args.get("customer_id"),
                conversation_id=state["conversation_id"],
                whatsapp_number=wa_number,
                channel=channel,
            )

        else:
            result = TOOLS_BY_NAME[name].invoke(args)

        content = result if isinstance(result, str) else json.dumps(result)
        outputs.append(ToolMessage(content=content, tool_call_id=call["id"]))

    return {"messages": outputs}


# --- Routing helpers -------------------------------------------------------


def _pending_list_orders_call(messages):
    """Return the most recent list_orders tool call in the message history."""
    for message in reversed(messages or []):
        for call in getattr(message, "tool_calls", None) or []:
            if call["name"] == "list_orders":
                return call
    return None


def _is_affirmative(value):
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {
            "yes",
            "y",
            "confirm",
            "confirmed",
            "ok",
            "okay",
            "sure",
            "yep",
            "yeah",
            "proceed",
        }
    return False


def _is_negative(value):
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {
            "no",
            "n",
            "nope",
            "cancel",
            "cancelled",
            "canceled",
            "stop",
            "abort",
            "nah",
        }
    return False


def route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    if not tool_calls:
        return END
    if any(call["name"] == "list_orders" for call in tool_calls):
        return "confirm"
    return "tools"


def route_after_confirm(state: AgentState) -> str:
    """Route out of confirm.

    * reconfirm set -> back into confirm to re-ask (ambiguous reply).
    * cancelled (confirm appended an AIMessage, no pending tool call) -> END.
    * confirmed -> tools, to actually run the lookup.
    """
    if state.get("reconfirm"):
        return "confirm"
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and not getattr(last, "tool_calls", None):
        return END
    return "tools"


# --- Graph assembly --------------------------------------------------------


def build_graph(checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("confirm", confirm_node)
    graph.add_node("tools", tools_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        route_after_agent,
        {"confirm": "confirm", "tools": "tools", END: END},
    )
    graph.add_conditional_edges(
        "confirm",
        route_after_confirm,
        {"confirm": "confirm", "tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")

    if checkpointer is None:
        checkpointer = DynamoDBSaver()
    return graph.compile(checkpointer=checkpointer)


# Build once at module scope for warm-container reuse.
compiled_graph = build_graph()


# --- Entry point -----------------------------------------------------------


def _is_paused(config):
    """Return True if the thread has a pending interrupt waiting to resume."""
    state = compiled_graph.get_state(config)
    return bool(state.next)


def _latest_text(config):
    """Return the text to show the user: an active interrupt or the last AI message."""
    state = compiled_graph.get_state(config)

    interrupts = getattr(state, "interrupts", None) or []
    if interrupts:
        payload = interrupts[-1].value
        if isinstance(payload, dict):
            return payload.get("message", json.dumps(payload))
        return str(payload)

    for message in reversed(state.values.get("messages", [])):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict)
                ]
                return "".join(parts)
    return ""


@log_io()
def run_agent(conversation_id, user_text, whatsapp_number=""):
    """Advance the conversation one turn and return the text to show the user.

    If the thread is paused on an interrupt, ``user_text`` resumes it via
    ``Command(resume=...)``. For a brand-new thread the full initial state is
    seeded. For an existing, non-paused thread only the new human message (and a
    refreshed whatsapp_number) is appended so ``conversation_id`` is never reset
    by a follow-up message.
    """
    config = {"configurable": {"thread_id": conversation_id}}

    if _is_paused(config):
        compiled_graph.invoke(Command(resume=user_text), config)
        return _latest_text(config)

    existing = compiled_graph.get_state(config)
    is_new_thread = not existing.values

    if is_new_thread:
        payload = {
            "messages": [HumanMessage(content=user_text)],
            "conversation_id": conversation_id,
            "whatsapp_number": whatsapp_number,
            "reconfirm": False,
        }
    else:
        # Append only; do NOT overwrite conversation_id.
        payload = {"messages": [HumanMessage(content=user_text)]}
        if whatsapp_number:
            payload["whatsapp_number"] = whatsapp_number

    compiled_graph.invoke(payload, config)
    return _latest_text(config)
