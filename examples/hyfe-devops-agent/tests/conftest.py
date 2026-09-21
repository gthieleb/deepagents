"""Pytest fixtures and self-contained stub tools for the Hyfe DevOps agent.

These stubs replace the production GitHub GraphQL and Slack Web API tools with
deterministic, synchronous implementations that return fixture data. No network
I/O or API keys are required.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from pydantic import Field


class FixedGenericFakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel variant that fixes two issues for test usage."""

    messages: Iterator[AIMessage | str] = Field(exclude=True)
    """Override parent field to exclude from pydantic serialization.

    Without this, tracing callbacks may dump the model via
    ``model_dump(mode="json")`` and consume the iterator before ``_generate``
    pulls from it.
    """

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Override bind_tools to return self."""
        return self


@tool
def get_board_state(
    project_number: int,
    *,
    org: str,
    max_in_scope: int = 10,
    max_in_progress: int = 3,
) -> str:
    """Return the current state of a GitHub Projects v2 board.

    Args:
        project_number: The GitHub Projects v2 project number.
        org: GitHub organization login that owns the project.
        max_in_scope: Advisory WIP limit for in-scope/todo/backlog items.
        max_in_progress: Advisory WIP limit for in-progress items.

    Returns:
        JSON string with items grouped by status, counts, and WIP violations.
    """
    return json.dumps(
        {
            "project_id": "PVT_123",
            "project_title": "Sprint 42",
            "total_items": 5,
            "items_by_status": {
                "In Progress": [
                    {
                        "item_id": "PVTI_1",
                        "content_type": "Issue",
                        "number": 42,
                        "title": "Fix auth timeout",
                        "state": "OPEN",
                        "url": "https://github.com/hyfe/test/issues/42",
                        "created_at": "2026-06-01T00:00:00Z",
                        "closed_at": None,
                        "status": "In Progress",
                        "labels": ["backend"],
                    }
                ],
                "Done": [
                    {
                        "item_id": "PVTI_2",
                        "content_type": "Issue",
                        "number": 41,
                        "title": "Update CI pipeline",
                        "state": "CLOSED",
                        "url": "https://github.com/hyfe/test/issues/41",
                        "created_at": "2026-05-28T00:00:00Z",
                        "closed_at": "2026-06-10T00:00:00Z",
                        "status": "Done",
                        "labels": ["devops"],
                    }
                ],
            },
            "counts": {"In Progress": 1, "Done": 1},
            "wip_limits": {
                "max_in_scope": max_in_scope,
                "max_in_progress": max_in_progress,
            },
            "wip_violations": [],
            "advisory_only": True,
        },
        default=str,
    )


@tool
def check_wip_limits(
    *,
    project_number: int,
    org: str,
    max_in_scope: int = 10,
    max_in_progress: int = 3,
) -> str:
    """Check whether a GitHub Projects v2 board exceeds configured WIP limits.

    Args:
        project_number: The GitHub Projects v2 project number.
        org: GitHub organization login that owns the project.
        max_in_scope: Advisory WIP limit for in-scope/todo/backlog items.
        max_in_progress: Advisory WIP limit for in-progress items.

    Returns:
        JSON string with counts and any WIP-limit violations (advisory only).
    """
    return json.dumps(
        {
            "project_number": project_number,
            "org": org,
            "total_items": 5,
            "in_scope_count": 4,
            "in_progress_count": 1,
            "wip_limits": {
                "max_in_scope": max_in_scope,
                "max_in_progress": max_in_progress,
            },
            "violations": [],
            "advisory_only": True,
        },
        default=str,
    )


@tool
def list_blocked_tickets(*, project_number: int, org: str) -> str:
    """Return project items that appear blocked by status or label.

    Args:
        project_number: The GitHub Projects v2 project number.
        org: GitHub organization login that owns the project.

    Returns:
        JSON string listing blocked items with status, labels, and URLs.
    """
    return json.dumps(
        {
            "project_number": project_number,
            "org": org,
            "blocked_count": 1,
            "blocked_items": [
                {
                    "item_id": "PVTI_3",
                    "content_type": "Issue",
                    "number": 43,
                    "title": "Blocked: roll out new ingress",
                    "state": "OPEN",
                    "url": "https://github.com/hyfe/test/issues/43",
                    "created_at": "2026-06-05T00:00:00Z",
                    "closed_at": None,
                    "status": "Blocked",
                    "labels": ["blocked", "infra"],
                }
            ],
        },
        default=str,
    )


@tool
def move_ticket(
    issue_number: int,
    target_status: str,
    *,
    project_number: int,
    org: str,
    repo: str,
) -> str:
    """Move a GitHub issue to a target status column in a Projects v2 board.

    The issue is added to the project if it is not already present. If the issue
    is already in the target status, the call is a no-op.

    Args:
        issue_number: Issue number to move.
        target_status: Target status name (case-insensitive).
        project_number: The GitHub Projects v2 project number.
        org: GitHub organization login that owns the project and repository.
        repo: Repository name containing the issue.

    Returns:
        JSON string with the updated issue and project item details.
    """
    return json.dumps(
        {
            "success": True,
            "issue_number": issue_number,
            "target_status": target_status,
            "project_number": project_number,
            "item_id": "PVTI_MOCK_123",
            "noop": False,
        },
        default=str,
    )


@tool
def parse_subtasks(issue_number: int, *, org: str, repo: str) -> str:
    """Parse checklist subtasks and linked sub-issues for a GitHub issue.

    Args:
        issue_number: Issue number to inspect.
        org: GitHub organization login that owns the repository.
        repo: Repository name containing the issue.

    Returns:
        JSON string with checklist completion stats and linked sub-issues.
    """
    return json.dumps(
        {
            "issue_number": issue_number,
            "total": 3,
            "completed": 2,
            "incomplete": 1,
            "completion_rate": 0.67,
            "checklist_items": [
                {
                    "text": "Update Terraform module",
                    "done": True,
                    "level": 0,
                    "indent": 0,
                },
                {"text": "Run staging plan", "done": True, "level": 0, "indent": 0},
                {
                    "text": "Approve production apply",
                    "done": False,
                    "level": 0,
                    "indent": 0,
                },
            ],
            "sub_issues": [],
            "sub_issue_count": 0,
        },
        default=str,
    )


@tool
def create_subtask(
    parent_issue_number: int,
    title: str,
    *,
    org: str,
    repo: str,
    body: str = "",
) -> str:
    """Create a subtask issue and link it to a parent GitHub issue.

    Uses the ``addSubIssue`` GraphQL mutation when available; otherwise falls
    back to referencing the parent issue in the subtask body.

    Args:
        parent_issue_number: Issue number to which the subtask will be linked.
        title: Title for the new subtask issue.
        org: GitHub organization login that owns the repository.
        repo: Repository name containing the parent issue.
        body: Optional body text for the new subtask issue.

    Returns:
        JSON string with the created issue number and URL.
    """
    return json.dumps(
        {
            "success": True,
            "parent_issue_number": parent_issue_number,
            "subtask_issue_number": 43,
            "url": f"https://github.com/{org}/{repo}/issues/43",
            "linked": True,
        },
        default=str,
    )


@tool
def get_sprint_velocity(
    *,
    org: str,
    repo: str,
    project_number: int,
    sprint_count: int = 3,
) -> str:
    """Compute recent sprint velocity from closed project items and subtasks.

    Sprints are defined as fixed 14-day windows counting backward from today.
    For each closed issue the subtask checklist completion rate and cycle time
    are calculated from the issue body and ``createdAt``/``closedAt`` fields.

    Args:
        org: GitHub organization login that owns the project and repository.
        repo: Repository name used for issue context links.
        project_number: The GitHub Projects v2 project number.
        sprint_count: Number of recent sprints to analyze.

    Returns:
        JSON string with per-sprint metrics and overall velocity trend.
    """
    return json.dumps(
        {
            "org": org,
            "repo": repo,
            "project_number": project_number,
            "sprint_count": sprint_count,
            "sprints": [
                {
                    "name": "Sprint 2026-05-26 to 2026-06-08",
                    "window_start": "2026-05-26T00:00:00+00:00",
                    "window_end": "2026-06-08T23:59:59.999999+00:00",
                    "total_issues": 5,
                    "closed_issues": 5,
                    "avg_subtask_completion": 1.0,
                    "avg_cycle_time_days": 2.5,
                },
                {
                    "name": "Sprint 2026-05-12 to 2026-05-25",
                    "window_start": "2026-05-12T00:00:00+00:00",
                    "window_end": "2026-05-25T23:59:59.999999+00:00",
                    "total_issues": 4,
                    "closed_issues": 4,
                    "avg_subtask_completion": 0.9,
                    "avg_cycle_time_days": 3.0,
                },
                {
                    "name": "Sprint 2026-04-28 to 2026-05-11",
                    "window_start": "2026-04-28T00:00:00+00:00",
                    "window_end": "2026-05-11T23:59:59.999999+00:00",
                    "total_issues": 6,
                    "closed_issues": 6,
                    "avg_subtask_completion": 1.0,
                    "avg_cycle_time_days": 2.0,
                },
            ],
            "overall_velocity": 5.0,
            "trend": "stable",
        },
        default=str,
    )


_EMOJI_PALETTE: list[str] = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
]


@tool
def send_notification(channel: str, message: str) -> str:
    """Post a plain-text notification to a Slack channel.

    Args:
        channel: Slack channel ID or name to post in. Falls back to
            ``SLACK_DEFAULT_CHANNEL`` if empty.
        message: Plain-text message body.

    Returns:
        JSON-serialized result with ``ok``, ``channel``, and ``ts`` fields,
        or an error object.
    """
    resolved_channel = channel or "C123"
    return json.dumps(
        {
            "ok": True,
            "channel": resolved_channel,
            "ts": "1234567890.123456",
            "message": "Notification sent.",
        }
    )


@tool
def post_sprint_summary(*, channel: str, summary: str) -> str:
    """Post a formatted sprint summary to a Slack channel.

    Args:
        channel: Slack channel ID or name to post in. Falls back to
            ``SLACK_DEFAULT_CHANNEL`` if empty.
        summary: Markdown sprint summary text.

    Returns:
        JSON-serialized result with ``ok``, ``channel``, and ``ts`` fields,
        or an error object.
    """
    resolved_channel = channel or "C123"
    return json.dumps(
        {
            "ok": True,
            "channel": resolved_channel,
            "ts": "1234567890.123456",
            "message": "Sprint summary posted.",
        }
    )


@tool
def create_slack_vote(question: str, options: list[str], *, channel: str) -> str:
    """Create an emoji-based poll in a Slack channel.

    Posts the question and options as a text message, then adds one reaction
    emoji per option. Returns the message timestamp so the vote can be
    evaluated later.

    Args:
        question: The poll question.
        options: List of answer options. Maximum 23 options.
        channel: Slack channel ID or name to post in. Falls back to
            ``SLACK_DEFAULT_CHANNEL`` if empty.

    Returns:
        JSON-serialized result with ``ok``, ``ts``, ``channel``,
        ``emoji_mapping``, and ``options``, or an error object.
    """
    resolved_channel = channel or "C123"
    emoji_mapping = {option: _EMOJI_PALETTE[i] for i, option in enumerate(options)}
    return json.dumps(
        {
            "ok": True,
            "channel": resolved_channel,
            "ts": "1234567890.123456",
            "question": question,
            "options": options,
            "emoji_mapping": emoji_mapping,
        }
    )


@tool
def evaluate_slack_vote(channel: str, message_ts: str, *, options: list[str]) -> str:
    """Evaluate an emoji-based poll previously created by ``create_slack_vote``.

    Reads reactions on the message, counts votes per option, and detects ties.
    A user who reacted to multiple option emojis is counted only for the
    first option in the provided ``options`` order. Non-option emojis and the
    bot's own reactions are excluded.

    Args:
        channel: Slack channel ID where the vote message was posted.
        message_ts: Timestamp ``ts`` of the vote message.
        options: The same option list passed to ``create_slack_vote``.

    Returns:
        JSON-serialized result with ``ok``, ``counts``, ``winners``, ``tie``,
        and ``total_voters``, or an error object.
    """
    counts: dict[str, int] = {}
    for i, option in enumerate(options):
        counts[option] = 3 if i == 0 else (1 if i == 1 else 0)

    max_votes = max(counts.values()) if counts else 0
    winners = [
        option for option, votes in counts.items() if votes == max_votes and votes > 0
    ]

    return json.dumps(
        {
            "ok": True,
            "channel": channel,
            "ts": message_ts,
            "counts": counts,
            "winners": winners,
            "tie": len(winners) > 1,
            "total_voters": sum(counts.values()),
        }
    )


@pytest.fixture
def board_tools() -> list:
    """Return the list of board stub tools."""
    return [
        get_board_state,
        check_wip_limits,
        list_blocked_tickets,
        move_ticket,
        parse_subtasks,
        create_subtask,
        get_sprint_velocity,
    ]


@pytest.fixture
def slack_tools() -> list:
    """Return the list of Slack stub tools."""
    return [
        send_notification,
        post_sprint_summary,
        create_slack_vote,
        evaluate_slack_vote,
    ]


@pytest.fixture
def all_stub_tools(board_tools: list, slack_tools: list) -> list:
    """Return all stub tools combined."""
    return board_tools + slack_tools


@pytest.fixture
def stub_agent(all_stub_tools: list) -> Any:
    """Return a Deep Agent wired with all stub tools and a fake chat model."""
    model = FixedGenericFakeChatModel(
        messages=iter([AIMessage(content="I am a stub assistant.")])
    )
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=model,
        tools=all_stub_tools,
        system_prompt="You are a DevOps assistant.",
    )
