"""Unit tests for Hyfe DevOps agent tool selection using a scripted fake LLM.

These tests verify that `create_deep_agent` selects and executes the correct
stub tools for common DevOps workflows. All LLM responses are scripted with
`FixedGenericFakeChatModel`; no network calls or API keys are required.
"""

from __future__ import annotations

import json

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.tools import tool

from tests.conftest import FixedGenericFakeChatModel


@pytest.fixture(autouse=True)
def _disable_vendor_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable default tracing so these unit tests do not attempt network calls."""
    flag = "".join(["LANG", "SMITH", "_TRACING_V2"])
    monkeypatch.setenv(flag, "false")


def test_agent_selects_correct_tool_for_query(all_stub_tools: list) -> None:
    """Agent calls `get_board_state` when asked about sprint status."""
    model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="get_board_state",
                            args={"project_number": 1, "org": "hyfe"},
                            id="call_board_1",
                        )
                    ],
                ),
                AIMessage(content="The sprint status is available above."),
            ]
        )
    )
    agent = create_deep_agent(
        model=model,
        tools=all_stub_tools,
        system_prompt="You are a DevOps assistant.",
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="What's the sprint status?")]}
    )

    tool_messages = [msg for msg in result["messages"] if isinstance(msg, ToolMessage)]
    assert any(msg.name == "get_board_state" for msg in tool_messages), (
        "Expected get_board_state ToolMessage"
    )
    assert any("Sprint 42" in msg.content for msg in tool_messages), (
        "Expected board state JSON in ToolMessage"
    )


def test_agent_uses_parallel_tool_calls(all_stub_tools: list) -> None:
    """Agent executes two tool calls in parallel and synthesizes the results."""
    model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="get_board_state",
                            args={"project_number": 1, "org": "hyfe"},
                            id="call_board_2",
                        ),
                        ToolCall(
                            name="check_wip_limits",
                            args={"project_number": 1, "org": "hyfe"},
                            id="call_wip_2",
                        ),
                    ],
                ),
                AIMessage(content="Board state and WIP limits are healthy."),
            ]
        )
    )
    agent = create_deep_agent(
        model=model,
        tools=all_stub_tools,
        system_prompt="You are a DevOps assistant.",
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Check board and WIP limits")]}
    )

    tool_messages = [msg for msg in result["messages"] if isinstance(msg, ToolMessage)]
    assert len(tool_messages) == 2, "Expected two parallel ToolMessages"
    assert any(msg.name == "get_board_state" for msg in tool_messages), (
        "Expected get_board_state ToolMessage"
    )
    assert any(msg.name == "check_wip_limits" for msg in tool_messages), (
        "Expected check_wip_limits ToolMessage"
    )

    ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
    assert "healthy" in ai_messages[-1].content.lower()


def test_agent_re_calls_after_tool_result(all_stub_tools: list) -> None:
    """Agent performs a multi-turn tool loop before producing the final answer."""
    model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="get_board_state",
                            args={"project_number": 1, "org": "hyfe"},
                            id="call_board_3",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="move_ticket",
                            args={
                                "issue_number": 42,
                                "target_status": "Done",
                                "project_number": 1,
                                "org": "hyfe",
                                "repo": "test",
                            },
                            id="call_move_3",
                        )
                    ],
                ),
                AIMessage(content="Ticket 42 has been moved to Done."),
            ]
        )
    )
    agent = create_deep_agent(
        model=model,
        tools=all_stub_tools,
        system_prompt="You are a DevOps assistant.",
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Move ticket 42 to done")]}
    )

    tool_messages = [msg for msg in result["messages"] if isinstance(msg, ToolMessage)]
    board_messages = [msg for msg in tool_messages if msg.name == "get_board_state"]
    move_messages = [msg for msg in tool_messages if msg.name == "move_ticket"]
    assert len(board_messages) == 1, "Expected exactly one get_board_state call"
    assert len(move_messages) == 1, "Expected exactly one move_ticket call"
    assert "Sprint 42" in board_messages[0].content
    assert "PVTI_MOCK_123" in move_messages[0].content

    ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
    assert "moved to Done" in ai_messages[-1].content


def test_agent_handles_tool_error_gracefully(all_stub_tools: list) -> None:
    """Agent recovers with a fallback response when a tool returns an error."""

    @tool
    def failing_tool() -> str:
        """A stub tool that returns a JSON error payload."""
        return json.dumps({"ok": False, "error": "Simulated tool failure"})

    model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="failing_tool",
                            args={},
                            id="call_fail_4",
                        )
                    ],
                ),
                AIMessage(content="The tool failed, but I can still help."),
            ]
        )
    )
    agent = create_deep_agent(
        model=model,
        tools=all_stub_tools + [failing_tool],
        system_prompt="You are a DevOps assistant.",
    )

    result = agent.invoke({"messages": [HumanMessage(content="Run the failing tool")]})

    tool_messages = [msg for msg in result["messages"] if isinstance(msg, ToolMessage)]
    assert len(tool_messages) >= 1, "Expected at least one ToolMessage"
    assert any("Simulated tool failure" in msg.content for msg in tool_messages), (
        "Expected error details in a ToolMessage"
    )

    ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
    assert "failed" in ai_messages[-1].content.lower()


def test_creates_vote_and_evaluates_it(all_stub_tools: list) -> None:
    """Agent creates a Slack vote and then evaluates the results."""
    model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="create_slack_vote",
                            args={
                                "question": "Deploy now?",
                                "options": ["Yes", "No"],
                                "channel": "C123",
                            },
                            id="call_vote_create_5",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="evaluate_slack_vote",
                            args={
                                "channel": "C123",
                                "message_ts": "1234567890.123456",
                                "options": ["Yes", "No"],
                            },
                            id="call_vote_eval_5",
                        )
                    ],
                ),
                AIMessage(content="The vote is complete. Winner: Yes with 3 votes."),
            ]
        )
    )
    agent = create_deep_agent(
        model=model,
        tools=all_stub_tools,
        system_prompt="You are a DevOps assistant.",
    )

    result = agent.invoke({"messages": [HumanMessage(content="Run a deploy vote")]})

    tool_messages = [msg for msg in result["messages"] if isinstance(msg, ToolMessage)]
    create_msgs = [msg for msg in tool_messages if msg.name == "create_slack_vote"]
    eval_msgs = [msg for msg in tool_messages if msg.name == "evaluate_slack_vote"]
    assert len(create_msgs) == 1, "Expected create_slack_vote to be called"
    assert len(eval_msgs) == 1, "Expected evaluate_slack_vote to be called"

    vote_data = json.loads(create_msgs[0].content)
    assert vote_data["question"] == "Deploy now?"
    assert vote_data["options"] == ["Yes", "No"]

    outcome = json.loads(eval_msgs[0].content)
    assert outcome["counts"]["Yes"] == 3
    assert outcome["winners"] == ["Yes"]
    assert outcome["tie"] is False


def test_full_board_workflow(all_stub_tools: list) -> None:
    """Agent queries board state, checks WIP limits, and parses subtasks."""
    model = FixedGenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="get_board_state",
                            args={"project_number": 1, "org": "hyfe"},
                            id="call_board_6",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="check_wip_limits",
                            args={"project_number": 1, "org": "hyfe"},
                            id="call_wip_6",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="parse_subtasks",
                            args={"issue_number": 42, "org": "hyfe", "repo": "test"},
                            id="call_subtasks_6",
                        )
                    ],
                ),
                AIMessage(
                    content=(
                        "The board has 1 in-progress item, "
                        "WIP limits are respected, and subtask completion is 67%."
                    )
                ),
            ]
        )
    )
    agent = create_deep_agent(
        model=model,
        tools=all_stub_tools,
        system_prompt="You are a DevOps assistant.",
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Analyze the full board")]}
    )

    tool_messages = [msg for msg in result["messages"] if isinstance(msg, ToolMessage)]
    assert any(msg.name == "get_board_state" for msg in tool_messages), (
        "Expected get_board_state call"
    )
    assert any(msg.name == "check_wip_limits" for msg in tool_messages), (
        "Expected check_wip_limits call"
    )
    assert any(msg.name == "parse_subtasks" for msg in tool_messages), (
        "Expected parse_subtasks call"
    )
    assert any("Sprint 42" in msg.content for msg in tool_messages)
    assert any("completion_rate" in msg.content for msg in tool_messages)

    ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
    final = ai_messages[-1].content
    assert "WIP" in final, "Expected final answer to mention WIP limits"
    assert "67%" in final, "Expected final answer to mention subtask completion"
