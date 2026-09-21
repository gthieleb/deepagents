"""Unit tests for the Hyfe DevOps orchestrator's subagent delegation.

These tests exercise ``create_deep_agent`` with the production subagent
configuration shape (dict-based ``subagents``) and the synchronous stub tools
from ``tests.conftest``.  No network calls, API keys, or real LLMs are used.
"""

from __future__ import annotations

import os

os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from deepagents import create_deep_agent
from tests.conftest import (
    FixedGenericFakeChatModel,
    check_wip_limits,
    get_board_state,
    get_sprint_velocity,
    list_blocked_tickets,
    parse_subtasks,
)


def test_orchestrator_delegates_to_sprint_coordinator() -> None:
    """Verify the orchestrator can delegate to the sprint-coordinator subagent.

    The orchestrator model first emits a ``task`` tool call targeting the
    ``sprint-coordinator`` subagent.  The subagent model returns a canned sprint
    health summary, which surfaces in the parent message history as a
    ``ToolMessage``.
    """
    sprint_tools = [
        get_board_state,
        check_wip_limits,
        list_blocked_tickets,
        parse_subtasks,
        get_sprint_velocity,
    ]

    subagent_model = FixedGenericFakeChatModel(
        messages=iter([AIMessage(content="Sprint status: healthy, 1 blocked ticket.")])
    )
    orchestrator_model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": "Analyze current sprint status",
                                "subagent_type": "sprint-coordinator",
                            },
                            "id": "call_sprint_coord_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="The sprint coordinator found the sprint is healthy."
                ),
            ]
        )
    )

    agent = create_deep_agent(
        model=orchestrator_model,
        tools=sprint_tools,
        system_prompt="You are a DevOps orchestrator.",
        subagents=[
            {
                "name": "sprint-coordinator",
                "description": "Analyzes sprint and board health.",
                "system_prompt": "You are a sprint coordinator.",
                "model": subagent_model,
                "tools": sprint_tools,
            }
        ],
        checkpointer=MemorySaver(),
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Check sprint status")]},
        config={"configurable": {"thread_id": "test_delegation_thread"}},
    )

    assert "messages" in result
    tool_messages = [msg for msg in result["messages"] if msg.type == "tool"]
    assert len(tool_messages) >= 1, "Expected at least one ToolMessage from subagent"
    assert "Sprint status: healthy" in tool_messages[0].content


def test_orchestrator_synthesizes_subagent_outputs() -> None:
    """Verify the orchestrator synthesizes outputs from multiple subagents.

    The orchestrator delegates sequentially to ``sprint-coordinator`` and
    ``infra-reviewer``.  The final orchestrator response must reference both
    subagent outputs, proving synthesis across subagent boundaries.
    """
    sprint_tools = [
        get_board_state,
        check_wip_limits,
        list_blocked_tickets,
        parse_subtasks,
        get_sprint_velocity,
    ]

    sprint_model = FixedGenericFakeChatModel(
        messages=iter([AIMessage(content="Sprint health: on track, 1 blocked ticket.")])
    )
    infra_model = FixedGenericFakeChatModel(
        messages=iter([AIMessage(content="Infra review: no risks detected.")])
    )
    orchestrator_model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": "Check sprint health",
                                "subagent_type": "sprint-coordinator",
                            },
                            "id": "call_sprint_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": "Review infrastructure changes",
                                "subagent_type": "infra-reviewer",
                            },
                            "id": "call_infra_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=(
                        "Synthesis: sprint health is on track and infra review "
                        "found no risks."
                    )
                ),
            ]
        )
    )

    agent = create_deep_agent(
        model=orchestrator_model,
        tools=sprint_tools,
        system_prompt="You are a DevOps orchestrator.",
        subagents=[
            {
                "name": "sprint-coordinator",
                "description": "Analyzes sprint and board health.",
                "system_prompt": "You are a sprint coordinator.",
                "model": sprint_model,
                "tools": sprint_tools,
            },
            {
                "name": "infra-reviewer",
                "description": "Reviews infrastructure changes.",
                "system_prompt": "You are an infrastructure reviewer.",
                "model": infra_model,
                "tools": [],
            },
        ],
        checkpointer=MemorySaver(),
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Check sprint and infra status")]},
        config={"configurable": {"thread_id": "test_synthesis_thread"}},
    )

    assert "messages" in result
    final_ai = [msg for msg in result["messages"] if isinstance(msg, AIMessage)][-1]
    assert "on track" in final_ai.content
    assert "no risks" in final_ai.content

    tool_messages = [msg for msg in result["messages"] if msg.type == "tool"]
    assert len(tool_messages) == 2, "Expected one ToolMessage per subagent"
    assert any("on track" in msg.content for msg in tool_messages)
    assert any("no risks" in msg.content for msg in tool_messages)


def test_orchestrator_handles_subagent_failure_gracefully() -> None:
    """Verify the orchestrator recovers when a subagent tool returns an error.

    The sprint-coordinator subagent model issues a call to a stub tool that
    returns a JSON error payload.  The subagent acknowledges the error, and the
    orchestrator returns a fallback response instead of crashing.  ``MemorySaver``
    is used to keep the multi-turn state stable.
    """

    @tool
    def failing_board_tool(project_number: int, *, org: str) -> str:
        """A stub tool that returns an error, simulating a subagent tool failure."""
        return f'{{"error": "simulated board tool failure for {org}#{project_number}"}}'

    sprint_tools = [
        get_board_state,
        check_wip_limits,
        list_blocked_tickets,
        parse_subtasks,
        get_sprint_velocity,
        failing_board_tool,
    ]

    subagent_model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "failing_board_tool",
                            "args": {"project_number": 2, "org": "hyfe"},
                            "id": "call_failing_tool",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Subagent failed: board tool error."),
            ]
        )
    )
    orchestrator_model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": "Check sprint status",
                                "subagent_type": "sprint-coordinator",
                            },
                            "id": "call_sprint_fail",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content=(
                        "Fallback: the sprint coordinator encountered an error; "
                        "proceeding with available board data only."
                    )
                ),
            ]
        )
    )

    agent = create_deep_agent(
        model=orchestrator_model,
        tools=sprint_tools,
        system_prompt="You are a DevOps orchestrator.",
        subagents=[
            {
                "name": "sprint-coordinator",
                "description": "Analyzes sprint and board health.",
                "system_prompt": "You are a sprint coordinator.",
                "model": subagent_model,
                "tools": sprint_tools,
            }
        ],
        checkpointer=MemorySaver(),
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Check sprint status")]},
        config={"configurable": {"thread_id": "test_failure_thread"}},
    )

    assert "messages" in result
    final_ai = [msg for msg in result["messages"] if isinstance(msg, AIMessage)][-1]
    assert "Fallback" in final_ai.content

    tool_messages = [msg for msg in result["messages"] if msg.type == "tool"]
    assert len(tool_messages) >= 1
    assert any("Subagent failed" in msg.content for msg in tool_messages)
