"""LangGraph agent core: state, tools, LLM, HITL interrupt, and run_agent.

The graph implements the ordering flow both triggers share:

1. Welcome + ask for the user ID. The LLM extracts the ID from whatever the
   user types (e.g. "my id is U1001") and calls the ``verify_user`` tool. The
   tool returns ``True`` when the id exists in the users table, which flips the
   conversation to authorized; otherwise it returns ``False`` and the assistant
   asks again. Until authorized, every OTHER tool call is blocked.
2. Once authorized, collect the order parameters (user_name, user_email,
   user_address, products, number_of_products).
3. Before the ``create_order`` tool runs, pause with ``interrupt(...)`` so the
   user can confirm the collected parameters. Only an explicit affirmative
   resume value lets the order proceed; anything else cancels.
4. ``create_order`` POSTs to the RPA order endpoint (including the
   conversation_id in the body), parses the workflow_id, and records the
   conversation->workflow mapping via ``data.put_mapping``.

Authorization is enforced in ``tools_node``: before the user is verified only
``verify_user`` may run; any other tool is refused with a message telling the
model to authorize first. ``user_id`` and ``whatsapp_number`` are taken from
graph state (never from LLM-supplied tool arguments) when the order is placed.
Every setting comes from the environment; nothing is hardcoded.
"""

import datetime
import json
import os
from typing import Annotated, Optional, TypedDict

import requests
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

import data
from checkpointer import DynamoDBSaver

DEFAULT_MODEL_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
ORDER_API_TIMEOUT = 30

SYSTEM_PROMPT = (
    "You are a friendly ordering assistant. Start every new conversation with a "
    "short welcome and ask the user for their user ID so you can authorize them. "
    "The user may give the ID conversationally (for example 'my id is U1001' or "
    "'ah, it's U1001') - extract just the ID token from their message and call "
    "the verify_user tool with it. Do NOT ask the user to retype a bare ID; you "
    "do the extracting. If verify_user returns authorized=false, apologise and "
    "ask them to double-check and send the ID again, then call verify_user with "
    "the new value. Do not collect any order details or call any other tool "
    "until verify_user has returned authorized=true.\n"
    "\n"
    "When verify_user returns authorized=true it also returns the user's stored "
    "profile (name, email, address). Each of these may be filled or empty:\n"
    "- For every profile field that is FILLED, do NOT ask for it from scratch. "
    "Instead show the stored value and ask the user to confirm it or change it, "
    "e.g. 'I'll ship to the name/email/address I have on file: <name>, <email>, "
    "<address>. Use these, or would you like to change any of them?'\n"
    "- For every profile field that is EMPTY (not on file), you MUST ask the "
    "user to provide it.\n"
    "Then collect the order itself: products and number_of_products.\n"
    "\n"
    "The final create_order arguments are: user_name, user_email, user_address "
    "(each either the confirmed stored value or the new value the user gave), "
    "plus products and number_of_products. When you have all five and the user "
    "has confirmed the ship-to details, call the create_order tool. A "
    "confirmation step will then show the full order and ask the user to answer "
    "yes or no before the order is actually placed.\n"
    "\n"
    "Use the get_current_time tool only if the user asks about the current time. "
    "Never ask the user for their user ID again once they are authorized."
)


# --- Graph state -----------------------------------------------------------


class AgentState(TypedDict):
    """Mutable state threaded through the graph for one conversation."""

    messages: Annotated[list, add_messages]
    authorized: bool
    user_id: Optional[str]
    conversation_id: str
    whatsapp_number: str
    # Profile prefilled from the users table at verification time. Empty string
    # means "not stored" -> the agent must ask the user for that field.
    profile_name: str
    profile_email: str
    profile_address: str
    # Set when a confirm reply was neither yes nor no, so the graph loops back
    # into the confirm node to re-ask for a clean yes/no.
    reconfirm: bool


# --- Tools -----------------------------------------------------------------


@tool
def verify_user(user_id: str) -> dict:
    """Authorize a user by their ID and return their stored profile.

    The model should extract the ID token from the user's message (which may be
    conversational, e.g. "my id is U1001") and pass just that token. Returns a
    dict ``{"authorized": bool, "profile": {...}}``. When authorized, ``profile``
    carries the stored ``user_name``/``user_email``/``user_address`` (any of
    which may be empty if not on file) so the assistant can confirm the filled
    ones with the user and only ask for the missing ones. No other tool may run
    until this returns ``authorized=true``.
    """
    return _verify_user_lookup((user_id or "").strip())


def _verify_user_lookup(candidate):
    """Shared lookup used by the tool and by tools_node (for state updates)."""
    if not candidate:
        return {"authorized": False, "profile": {}}
    item = data.get_user(candidate)
    if not item:
        return {"authorized": False, "profile": {}}
    return {
        "authorized": True,
        "profile": {
            "user_name": item.get("user_name", "") or "",
            "user_email": item.get("user_email", "") or "",
            "user_address": item.get("user_address", "") or "",
        },
    }


@tool
def get_current_time() -> str:
    """Return the current UTC time in ISO 8601 and a human-readable form."""
    now = datetime.datetime.now(datetime.timezone.utc)
    iso = now.isoformat()
    human = now.strftime("%A, %d %B %Y at %H:%M:%S UTC")
    return f"Current time: {iso} ({human})"


@tool
def create_order(
    user_name: str,
    user_email: str,
    user_address: str,
    products: str,
    number_of_products: int,
    conversation_id: str,
) -> dict:
    """Start an order workflow via the RPA order endpoint.

    POSTs the order details (including ``conversation_id``) to the configured
    order API, parses the returned ``workflow_id`` and records the
    conversation->workflow mapping. ``user_id`` and ``whatsapp_number`` are
    injected from graph state, not from the model's arguments.
    """
    return place_order(
        user_name=user_name,
        user_email=user_email,
        user_address=user_address,
        products=products,
        number_of_products=number_of_products,
        conversation_id=conversation_id,
        user_id=None,
        whatsapp_number="",
    )


def place_order(
    *,
    user_name,
    user_email,
    user_address,
    products,
    number_of_products,
    conversation_id,
    user_id,
    whatsapp_number,
):
    """Perform the order POST + mapping write using state-sourced identity.

    Separated from the ``create_order`` tool so the executing node can supply
    ``user_id``/``whatsapp_number`` from graph state rather than trusting the
    model. Returns a dict with ``workflow_id`` and a user-facing confirmation.
    """
    endpoint = os.environ["ORDER_API_ENDPOINT"]
    body = {
        "user_name": user_name,
        "user_email": user_email,
        "user_address": user_address,
        "products": products,
        "number_of_products": number_of_products,
        "conversation_id": conversation_id,
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

    response = requests.post(
        endpoint, json=body, headers=headers, timeout=ORDER_API_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    workflow_id = payload.get("workflow_id")

    data.put_mapping(conversation_id, workflow_id, user_id, whatsapp_number)

    return {
        "workflow_id": workflow_id,
        "message": (
            f"Your order has been started (workflow {workflow_id}). "
            "You'll receive an update once it finishes."
        ),
    }


TOOLS = [verify_user, get_current_time, create_order]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


# --- LLM -------------------------------------------------------------------


def _build_llm():
    model_id = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    region = os.environ.get("AWS_REGION", "us-east-1")
    return ChatBedrockConverse(model=model_id, region_name=region).bind_tools(TOOLS)


# --- Nodes -----------------------------------------------------------------


def agent_node(state: AgentState) -> dict:
    """Call the tool-bound LLM with the system prompt prepended."""
    llm = _build_llm()
    messages = [("system", SYSTEM_PROMPT), *state["messages"]]
    response = llm.invoke(messages)
    return {"messages": [response]}


def _format_order_summary(params):
    """Build the human-readable order summary shown at the confirm step."""
    lines = [
        "Here is your order:",
        "",
        f"- Name: {params.get('user_name', '')}",
        f"- Email: {params.get('user_email', '')}",
        f"- Address: {params.get('user_address', '')}",
        f"- Products: {params.get('products', '')}",
        f"- Number of products: {params.get('number_of_products', '')}",
        "",
        "Please confirm you want to place this order. (enter: yes or no)",
    ]
    return "\n".join(lines)


def confirm_node(state: AgentState) -> dict:
    """Pause before create_order so the user can confirm the parameters.

    Shows the full order summary and asks the user to answer **yes or no**.
    ``interrupt`` pauses until the user replies:
      * an affirmative (yes/ok/confirm/...) proceeds to place the order,
      * a clear negative (no/cancel/stop/...) cancels,
      * anything ambiguous re-prompts (loops the interrupt) instead of
        silently cancelling, so a stray reply never drops the order.
    """
    tool_call = _pending_create_order_call(state["messages"])
    params = dict(tool_call["args"]) if tool_call else {}
    summary = _format_order_summary(params)

    # One interrupt per entry into the node. On an ambiguous reply the node
    # returns ``reconfirm=True`` and the graph routes straight back here, which
    # raises a brand-new interrupt so the thread pauses again for a clean yes/no
    # (instead of silently cancelling or placing the order).
    if state.get("reconfirm"):
        prompt = (
            "Sorry, I didn't catch that - please answer 'yes' to place the "
            "order or 'no' to cancel.\n\n" + summary
        )
    else:
        prompt = summary

    decision = interrupt(
        {
            "type": "confirm_order",
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
                    content="Order cancelled by the user.",
                    tool_call_id=tool_call["id"] if tool_call else "cancelled",
                ),
                AIMessage(
                    content=(
                        "No problem, I've cancelled that order. Let me know if "
                        "you'd like to change anything or try again."
                    )
                ),
            ],
        }

    # Ambiguous: flag for a re-ask and route back into confirm.
    return {"reconfirm": True}


def tools_node(state: AgentState) -> dict:
    """Execute the tools requested by the last AI message.

    Authorization gate: until the conversation is authorized, the only tool
    allowed to run is ``verify_user``. Any other tool call is refused with a
    ToolMessage telling the model to authorize first. ``verify_user`` returning
    ``True`` flips ``authorized`` and records the verified ``user_id`` in state.

    For ``create_order`` the identity fields (user_id, whatsapp_number) and the
    conversation_id come from graph state, not the model's tool arguments.
    """
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    outputs = []
    authorized = bool(state.get("authorized"))
    new_user_id = None
    new_profile = None

    for call in tool_calls:
        name = call["name"]
        args = dict(call.get("args", {}))

        if name == "verify_user":
            candidate = (args.get("user_id") or "").strip()
            result = _verify_user_lookup(candidate)
            if result["authorized"]:
                authorized = True
                new_user_id = candidate
                new_profile = result["profile"]

        elif not authorized:
            # Gate every non-verify tool until the user is authorized.
            result = {
                "error": "not_authorized",
                "message": (
                    "The user is not authorized yet. Ask for their user ID and "
                    "call verify_user first."
                ),
            }

        elif name == "create_order":
            result = place_order(
                user_name=args.get("user_name"),
                user_email=args.get("user_email"),
                user_address=args.get("user_address"),
                products=args.get("products"),
                number_of_products=args.get("number_of_products"),
                conversation_id=state["conversation_id"],
                user_id=state.get("user_id") or new_user_id,
                whatsapp_number=state.get("whatsapp_number", ""),
            )

        else:
            result = TOOLS_BY_NAME[name].invoke(args)

        content = result if isinstance(result, str) else json.dumps(result)
        outputs.append(ToolMessage(content=content, tool_call_id=call["id"]))

    update = {"messages": outputs}
    if authorized and not state.get("authorized"):
        update["authorized"] = True
    if new_user_id:
        update["user_id"] = new_user_id
    if new_profile is not None:
        update["profile_name"] = new_profile["user_name"]
        update["profile_email"] = new_profile["user_email"]
        update["profile_address"] = new_profile["user_address"]
    return update


# --- Routing helpers -------------------------------------------------------


def _pending_create_order_call(messages):
    """Return the most recent create_order tool call in the message history."""
    for message in reversed(messages or []):
        for call in getattr(message, "tool_calls", None) or []:
            if call["name"] == "create_order":
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
    if any(call["name"] == "create_order" for call in tool_calls):
        return "confirm"
    return "tools"


def route_after_confirm(state: AgentState) -> str:
    """Route out of confirm.

    * reconfirm set -> back into confirm to re-ask (ambiguous reply).
    * cancelled (confirm appended an AIMessage, no pending tool call) -> END.
    * confirmed -> tools, to actually place the order.
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


def run_agent(conversation_id, user_text, whatsapp_number=""):
    """Advance the conversation one turn and return the text to show the user.

    If the thread is paused on an interrupt, ``user_text`` resumes it via
    ``Command(resume=...)``. For a brand-new thread the full initial state is
    seeded (authorized=False). For an existing, non-paused thread only the new
    human message (and a refreshed whatsapp_number) is appended — the
    ``authorized``/``user_id`` channels are preserved so authorization persists
    across turns and is never reset by a follow-up message.
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
            "authorized": False,
            "user_id": None,
            "profile_name": "",
            "profile_email": "",
            "profile_address": "",
            "reconfirm": False,
        }
    else:
        # Append only; do NOT overwrite authorized/user_id/conversation_id.
        payload = {"messages": [HumanMessage(content=user_text)]}
        if whatsapp_number:
            payload["whatsapp_number"] = whatsapp_number

    compiled_graph.invoke(payload, config)
    return _latest_text(config)
