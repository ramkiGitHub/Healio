"""
app/graph/graph.py
==================
LangGraph pipeline assembly and the public ``run_graph()`` entry point.

Why this file exists
--------------------
This file ties everything together:
1. Creates the ``StateGraph`` (the LangGraph directed graph).
2. Registers all nodes from ``nodes.py``.
3. Registers all conditional edges from ``edges.py``.
4. Compiles the graph with a SQLite checkpointer for persistent multi-turn memory.
5. Exposes ``run_graph()`` — the single function that all channel handlers call
   to process a patient message end-to-end.

How memory works
----------------
LangGraph's ``SqliteSaver`` checkpointer persists the full ``HealioState``
to SQLite after every graph execution, keyed by ``thread_id`` (= session_id).
On the next turn from the same patient, the state is automatically restored,
giving Healio true multi-turn memory without any manual state management.

Database: ``data/db/healio.db`` (configured via ``DATABASE_URL`` in ``.env``).

How to extend
-------------
- Add a new node: ``graph.add_node("my_node", my_node_function)``
- Add a new edge: ``graph.add_conditional_edges("source_node", routing_fn, {...})``
- Recompile after any structural change: the ``_compiled_graph`` singleton
  will be rebuilt on the next call to ``_get_compiled_graph()``.

Usage
-----
    from app.graph.graph import run_graph
    from app.channels.normalizer import IncomingMessage

    reply = await run_graph(incoming_message)
"""

import os
import sqlite3
from functools import lru_cache

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.config import settings
from app.graph.edges import (
    NODE_EMERGENCY,
    NODE_GENERAL_QA,
    NODE_PROFILE_LOOKUP,
    NODE_ROUTER,
    NODE_SCHEDULE,
    route_after_emergency,
    route_after_profile_lookup,
    route_after_router,
)
from app.graph.nodes import (
    emergency_node,
    general_qa_node,
    profile_lookup_node,
    router_node,
    schedule_node,
)
from app.graph.state import HealioState, create_initial_state
from app.logging_config import get_logger

log = get_logger(__name__)


# ── Graph compilation ──────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    """Build the Healio StateGraph with all nodes and edges.

    This function constructs the graph topology. It is called once at startup
    and the result is compiled into an executable graph.

    The graph structure:
    ┌─────────────────────────────────────────────────────────────────┐
    │  START → router_node                                            │
    │           ↓ (conditional: route_after_router)                   │
    │    ┌──────┼────────────┬──────────────┐                         │
    │    ▼      ▼            ▼              ▼                         │
    │ emergency profile_   schedule      general_qa                   │
    │  _node   lookup_node  _node          _node                      │
    │    ↓      ↓ (cond)    ↓              ↓                          │
    │   END  general_qa   END             END                         │
    │         or schedule                                             │
    └─────────────────────────────────────────────────────────────────┘

    Returns:
        An uncompiled ``StateGraph`` ready for ``.compile()``.
    """
    graph = StateGraph(HealioState)

    # ── Register nodes ──────────────────────────────────────────────────────
    graph.add_node(NODE_ROUTER, router_node)
    graph.add_node(NODE_EMERGENCY, emergency_node)
    graph.add_node(NODE_PROFILE_LOOKUP, profile_lookup_node)
    graph.add_node(NODE_SCHEDULE, schedule_node)
    graph.add_node(NODE_GENERAL_QA, general_qa_node)

    # ── Entry point ─────────────────────────────────────────────────────────
    # Every conversation turn starts at the router node
    graph.set_entry_point(NODE_ROUTER)

    # ── Conditional edge: Router → next node ────────────────────────────────
    # route_after_router() inspects intent + severity and returns the next node name
    graph.add_conditional_edges(
        NODE_ROUTER,
        route_after_router,
        {
            NODE_EMERGENCY: NODE_EMERGENCY,
            NODE_SCHEDULE: NODE_SCHEDULE,
            NODE_PROFILE_LOOKUP: NODE_PROFILE_LOOKUP,
            NODE_GENERAL_QA: NODE_GENERAL_QA,
        },
    )

    # ── Conditional edge: ProfileLookup → next node ─────────────────────────
    # After loading the profile, re-route based on the original intent
    graph.add_conditional_edges(
        NODE_PROFILE_LOOKUP,
        route_after_profile_lookup,
        {
            NODE_GENERAL_QA: NODE_GENERAL_QA,
            NODE_SCHEDULE: NODE_SCHEDULE,
        },
    )

    # ── Conditional edge: Emergency → END ───────────────────────────────────
    graph.add_conditional_edges(
        NODE_EMERGENCY,
        route_after_emergency,
        {END: END},
    )

    # ── Terminal edges: all response nodes → END ────────────────────────────
    # These nodes produce the final patient reply and terminate the graph.
    graph.add_edge(NODE_GENERAL_QA, END)
    graph.add_edge(NODE_SCHEDULE, END)

    return graph


def _get_checkpointer() -> SqliteSaver:
    """Create and return a SQLite checkpointer for LangGraph memory persistence.

    The checkpointer saves the full ``HealioState`` to SQLite after every
    graph execution. The database path is derived from ``DATABASE_URL``
    in settings.

    Returns:
        A configured ``SqliteSaver`` instance.

    Note:
        For PostgreSQL migration (post-MVP), replace ``SqliteSaver`` with
        ``AsyncPostgresSaver`` from ``langgraph.checkpoint.postgres``.
        The database URL is already set up to accept a postgres:// URL.
    """
    # Extract file path from the SQLite URL
    # e.g., "sqlite+aiosqlite:///./data/db/healio.db" → "./data/db/healio.db"
    db_url = settings.database_url
    db_path = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")

    # Ensure directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    log.info("checkpointer_initialised", db_path=db_path)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)


@lru_cache(maxsize=1)
def _get_compiled_graph() -> CompiledStateGraph:
    """Compile and return the singleton compiled LangGraph graph.

    Uses ``lru_cache`` so the graph is built and compiled only once at
    startup, not on every request.

    Returns:
        A compiled ``CompiledStateGraph`` ready to invoke.
    """
    log.info("compiling_healio_graph")
    graph = _build_graph()
    checkpointer = _get_checkpointer()
    compiled = graph.compile(checkpointer=checkpointer)
    log.info("healio_graph_compiled")
    return compiled


# ── Public entry point ─────────────────────────────────────────────────────────

async def  run_graph(incoming) -> str:  # type: ignore[no-untyped-def]
    """Process a patient message through the Healio LangGraph pipeline.

    This is the single public function that all channel handlers (Telegram,
    WhatsApp) call to get an AI response for a patient message.

    On the first turn for a session, a new ``HealioState`` is created.
    On subsequent turns, the existing state is restored from the SQLite
    checkpoint — giving the conversation full multi-turn memory.

    Args:
        incoming: An ``IncomingMessage`` DTO from ``app/channels/normalizer.py``.
                  Contains: session_id, patient_id, text, channel.

    Returns:
        The AI-generated reply text string to send back to the patient.

    Raises:
        NodeExecutionError: If a graph node fails unexpectedly.
        GraphError: If the graph execution fails at a higher level.

    Example:
        >>> from app.channels.normalizer import normalize_telegram
        >>> msg = normalize_telegram(sender_id=123, text="I have a headache")
        >>> reply = await run_graph(msg)
        >>> print(reply)
        'That sounds uncomfortable. Headaches can have many causes...'
    """
    compiled_graph = _get_compiled_graph()
    session_id = incoming.session_id
    patient_id = incoming.patient_id

    log.info(
        "run_graph_started",
        session_id=session_id,
        patient_id=patient_id,
        text_preview=incoming.text[:80],
    )

    # LangGraph config — thread_id is the key for SQLite memory checkpointing
    config = {
        "configurable": {
            "thread_id": session_id,
        }
    }

    # Check if this session already has a checkpoint (returning patient)
    # If so, we only need to send the new message — LangGraph restores the rest.
    existing_state = compiled_graph.get_state(config)

    if existing_state and existing_state.values:
        # Returning patient — append the new message to existing conversation
        input_state = {"messages": [HumanMessage(content=incoming.text)]}
        log.info("run_graph_resuming_session", session_id=session_id)
    else:
        # New patient — create full initial state
        input_state = create_initial_state(
            session_id=session_id,
            patient_id=patient_id,
            first_message=incoming.text,
        )
        log.info("run_graph_new_session", session_id=session_id)

    # Execute the graph (synchronous invoke — LangGraph handles async internally)
    # NOTE: For fully async execution, use `ainvoke` once LangGraph async
    # checkpointers are production-stable. For now, this is safe in FastAPI
    # with asyncio.to_thread if needed.
    final_state = compiled_graph.invoke(input_state, config=config)

    # Extract the last AI message as the reply to send to the patient
    reply_text = _extract_last_ai_message(final_state)

    log.info(
        "run_graph_complete",
        session_id=session_id,
        reply_preview=reply_text[:80],
    )

    return reply_text


# ── Helper functions ───────────────────────────────────────────────────────────

def _extract_last_ai_message(final_state: dict) -> str:
    """Extract the last AI-generated message from the final graph state.

    Args:
        final_state: The final ``HealioState`` dict after graph execution.

    Returns:
        The text content of the last ``AIMessage`` in the messages list.
        Falls back to a generic error message if no AI message is found.
    """
    from langchain_core.messages import AIMessage

    messages = final_state.get("messages", [])
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return str(message.content)

    log.warning("no_ai_message_in_final_state")
    return (
        "I'm sorry, I wasn't able to process your message. "
        "Please try again or contact the clinic directly."
    )


# ── LangGraph Cloud entry point ────────────────────────────────────────────────
# LangGraph Cloud / LangGraph Studio resolve the graph by loading the Python
# module and reading this attribute.  The lru_cache ensures it is built once.
#
# Referenced in langgraph.json:
#   "graphs": { "healio": "./app/graph/graph.py:healio_graph" }
healio_graph: CompiledStateGraph = _get_compiled_graph()
