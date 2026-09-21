"""Integration evals for the Hyfe DevOps agent using DeepEval metrics.

These tests exercise the real GLM-4.5 endpoint through Z.AI against the
synchronous stub tools defined in ``tests.conftest``.  No GitHub or Slack
APIs are called.  Tests are skipped unless ``ZAI_API_KEY`` is present.
"""

from __future__ import annotations

import os

# Suppress DeepEval API-key and telemetry warnings before any deepeval imports.
os.environ["DEEPEVAL_NO_API_KEY"] = "true"
os.environ["DEEPEVAL_TELEMETRY"] = "off"
# Ensure the agent runtime does not attempt to send traces externally.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
# ToolCorrectnessMetric defaults to an OpenAI judge model during initialization
# even though this test does not use it (no available_tools are provided).
# Provide a placeholder so the metric can instantiate without a real key.
os.environ.setdefault("OPENAI_API_KEY", "dummy-openai-key-for-deepeval-init")

from typing import Any

import pytest
from deepeval.integrations.langchain import CallbackHandler
from deepeval.metrics import TaskCompletionMetric, ToolCorrectnessMetric
from deepeval.test_case import LLMTestCase, ToolCall
from langchain_openai import ChatOpenAI

from deepagents import create_deep_agent
from tests.conftest import (
    check_wip_limits,
    create_slack_vote,
    create_subtask,
    evaluate_slack_vote,
    get_board_state,
    get_sprint_velocity,
    list_blocked_tickets,
    move_ticket,
    parse_subtasks,
    post_sprint_summary,
    send_notification,
)


_SKIP_REASON = "ZAI_API_KEY not set"
_SYSTEM_PROMPT = (
    "You are a DevOps assistant. Use the available tools to answer questions. "
    "When querying GitHub Projects, use project_number=1 and org='hyfe'. "
    "For repository-specific queries, use org='hyfe' and repo='test'. "
    "For Slack actions, use channel='C123'. "
    "When assessing release readiness, check board state, blocked tickets, "
    "and parse subtasks for issue_number=42 to verify completion."
)


def _all_stub_tools() -> list:
    """Return all stub tools as a flat list.

    This helper avoids relying on the ``all_stub_tools`` pytest fixture from
    ``tests/conftest`` so the tests can be invoked with minimal fixture wiring.
    """
    return [
        get_board_state,
        check_wip_limits,
        list_blocked_tickets,
        move_ticket,
        parse_subtasks,
        create_subtask,
        get_sprint_velocity,
        send_notification,
        post_sprint_summary,
        create_slack_vote,
        evaluate_slack_vote,
    ]


def _make_model() -> ChatOpenAI:
    """Create a ``ChatOpenAI`` instance backed by the Z.AI GLM endpoint."""
    return ChatOpenAI(
        model="glm-4.5",
        openai_api_key=os.environ["ZAI_API_KEY"],
        openai_api_base="https://api.z.ai/api/coding/paas/v4/",
        temperature=0.0,
    )


def _extract_tool_calls(result: dict[str, Any]) -> list[ToolCall]:
    """Extract DeepEval ``ToolCall`` objects from the agent result messages."""
    tools_called: list[ToolCall] = []
    for msg in result.get("messages", []):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tools_called.append(
                    ToolCall(
                        name=tc.get("name", ""),
                        args=tc.get("args", {}),
                    )
                )
    return tools_called


def _build_test_case(
    query: str,
    result: dict[str, Any],
    expected_tool_names: list[str],
) -> LLMTestCase:
    """Build an ``LLMTestCase`` for ToolCorrectnessMetric validation."""
    messages = result.get("messages", [])
    actual_output = messages[-1].content if messages else ""
    return LLMTestCase(
        input=query,
        actual_output=actual_output,
        tools_called=_extract_tool_calls(result),
        expected_tools=[ToolCall(name=name, args={}) for name in expected_tool_names],
    )


def _assert_tool_correctness(
    metric: ToolCorrectnessMetric,
    test_case: LLMTestCase,
) -> None:
    """Measure the test case and assert the tool-correctness metric passes."""
    metric.measure(test_case)
    assert metric.is_successful(), (
        f"ToolCorrectnessMetric failed: {metric.reason}"
    )


@pytest.mark.eval
@pytest.mark.skipif(not os.environ.get("ZAI_API_KEY"), reason=_SKIP_REASON)
def test_sprint_status_workflow(
    deepeval_callback: CallbackHandler,
    tool_correctness_metric: ToolCorrectnessMetric,
    task_completion_metric: TaskCompletionMetric,
) -> None:
    """Agent calls ``get_board_state`` to answer a sprint-status query."""
    agent = create_deep_agent(
        model=_make_model(),
        tools=_all_stub_tools(),
        system_prompt=_SYSTEM_PROMPT,
    )

    query = "What's the current sprint status?"
    result = agent.invoke(
        {"messages": [("human", query)]},
        config={"callbacks": [deepeval_callback]},
    )

    test_case = _build_test_case(
        query=query,
        result=result,
        expected_tool_names=["get_board_state"],
    )
    _assert_tool_correctness(tool_correctness_metric, test_case)
    task_completion_metric.measure(test_case)
    assert task_completion_metric.is_successful(), (
        f"TaskCompletionMetric failed: {task_completion_metric.reason}"
    )


@pytest.mark.eval
@pytest.mark.skipif(not os.environ.get("ZAI_API_KEY"), reason=_SKIP_REASON)
def test_vote_creation_and_evaluation(
    deepeval_callback: CallbackHandler,
    tool_correctness_metric: ToolCorrectnessMetric,
    task_completion_metric: TaskCompletionMetric,
) -> None:
    """Agent calls ``create_slack_vote`` to set up a database poll."""
    agent = create_deep_agent(
        model=_make_model(),
        tools=_all_stub_tools(),
        system_prompt=_SYSTEM_PROMPT,
    )

    query = "Create a vote for which database to use: Postgres or MySQL"
    result = agent.invoke(
        {"messages": [("human", query)]},
        config={"callbacks": [deepeval_callback]},
    )

    test_case = _build_test_case(
        query=query,
        result=result,
        expected_tool_names=["create_slack_vote"],
    )
    _assert_tool_correctness(tool_correctness_metric, test_case)
    task_completion_metric.measure(test_case)
    assert task_completion_metric.is_successful(), (
        f"TaskCompletionMetric failed: {task_completion_metric.reason}"
    )


@pytest.mark.eval
@pytest.mark.skipif(not os.environ.get("ZAI_API_KEY"), reason=_SKIP_REASON)
def test_release_readiness(
    deepeval_callback: CallbackHandler,
    tool_correctness_metric: ToolCorrectnessMetric,
    task_completion_metric: TaskCompletionMetric,
) -> None:
    """Agent gathers board state, blocked tickets, and subtasks for release readiness."""
    agent = create_deep_agent(
        model=_make_model(),
        tools=_all_stub_tools(),
        system_prompt=_SYSTEM_PROMPT,
    )

    query = "Is the release ready? Check blocking issues and PRs"
    result = agent.invoke(
        {"messages": [("human", query)]},
        config={"callbacks": [deepeval_callback]},
    )

    test_case = _build_test_case(
        query=query,
        result=result,
        expected_tool_names=[
            "get_board_state",
            "list_blocked_tickets",
            "parse_subtasks",
        ],
    )
    _assert_tool_correctness(tool_correctness_metric, test_case)
    task_completion_metric.measure(test_case)
    assert task_completion_metric.is_successful(), (
        f"TaskCompletionMetric failed: {task_completion_metric.reason}"
    )
