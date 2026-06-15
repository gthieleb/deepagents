"""Hyfe DevOps Agent - LangGraph dev entry point.

Uses Z.AI GLM via OpenAI-compatible endpoint (coding plan).
Coordinates sync sprint-coordinator, infra-reviewer, and release-manager subagents.
"""

import os
from typing import Any

from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent
from langfuse.langchain import CallbackHandler

from tools.board_tools import (
    get_board_state,
    move_ticket,
    check_wip_limits,
    list_blocked_tickets,
    parse_subtasks,
    create_subtask,
    get_sprint_velocity,
)
from tools.slack_tools import (
    send_notification,
    post_sprint_summary,
    create_slack_vote,
    evaluate_slack_vote,
)

# Lazy agent construction so that `import agent` succeeds without requiring
# runtime credentials. LangGraph dev accesses the `agent` attribute, which
# triggers construction on first use.
_AGENT: Any | None = None

_SUBAGENT_SPECS: dict[str, dict[str, Any] | None] = {
    "sprint_coordinator_subagent": None,
    "infra_reviewer_subagent": None,
    "release_manager_subagent": None,
}


def _load_prompt(path: str) -> str:
    with open(path) as f:
        return f.read()


def _build_agent() -> Any:
    global _AGENT
    if _AGENT is not None:
        return _AGENT

    system_prompt = _load_prompt(
        os.path.join(os.path.dirname(__file__), "AGENTS.md")
    )
    sprint_coordinator_prompt = _load_prompt(
        os.path.join(
            os.path.dirname(__file__), "subagents", "sprint-coordinator", "AGENTS.md"
        )
    )
    infra_reviewer_prompt = _load_prompt(
        os.path.join(
            os.path.dirname(__file__), "subagents", "infra-reviewer", "AGENTS.md"
        )
    )
    release_manager_prompt = _load_prompt(
        os.path.join(
            os.path.dirname(__file__), "subagents", "release-manager", "AGENTS.md"
        )
    )

    langfuse_handler = CallbackHandler()

    model = ChatOpenAI(
        model="glm-4.5",
        openai_api_key=os.getenv("ZAI_API_KEY"),
        openai_api_base="https://api.z.ai/api/coding/paas/v4/",
        temperature=0.0,
        callbacks=[langfuse_handler],
    )

    subagent_model = ChatOpenAI(
        model="glm-4.5",
        openai_api_key=os.getenv("ZAI_API_KEY"),
        openai_api_base="https://api.z.ai/api/coding/paas/v4/",
        temperature=0.0,
    )

    sprint_coordinator_subagent = {
        "name": "sprint-coordinator",
        "model": subagent_model,
        "description": (
            "Analyzes sprint and board health using GitHub Projects tools including "
            "subtask parsing and velocity tracking. Returns structured sprint status "
            "with WIP violations, blocked items, subtask completion rates, and "
            "velocity trends."
        ),
        "system_prompt": sprint_coordinator_prompt,
        "tools": [
            get_board_state,
            check_wip_limits,
            list_blocked_tickets,
            parse_subtasks,
            get_sprint_velocity,
        ],
    }

    infra_reviewer_subagent = {
        "name": "infra-reviewer",
        "model": subagent_model,
        "description": (
            "Reviews infrastructure configurations including Terraform plans, "
            "Kubernetes manifests, and CI/CD pipelines. Returns risk assessment "
            "with security concerns and drift detection."
        ),
        "system_prompt": infra_reviewer_prompt,
        "tools": [],
    }

    release_manager_subagent = {
        "name": "release-manager",
        "model": subagent_model,
        "description": (
            "Assesses release readiness by checking open issues, subtask completion, "
            "blocking PRs, and CI status. Returns go/no-go checklist with risks "
            "and prerequisites."
        ),
        "system_prompt": release_manager_prompt,
        "tools": [get_board_state, parse_subtasks, send_notification],
    }

    _SUBAGENT_SPECS["sprint_coordinator_subagent"] = sprint_coordinator_subagent
    _SUBAGENT_SPECS["infra_reviewer_subagent"] = infra_reviewer_subagent
    _SUBAGENT_SPECS["release_manager_subagent"] = release_manager_subagent

    _AGENT = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        subagents=[
            sprint_coordinator_subagent,
            infra_reviewer_subagent,
            release_manager_subagent,
        ],
        tools=[
            get_board_state,
            move_ticket,
            check_wip_limits,
            list_blocked_tickets,
            parse_subtasks,
            create_subtask,
            get_sprint_velocity,
            send_notification,
            post_sprint_summary,
            create_slack_vote,
            evaluate_slack_vote,
        ],
    )
    return _AGENT


def __getattr__(name: str) -> Any:
    if name == "agent":
        return _build_agent()
    if name in _SUBAGENT_SPECS:
        _build_agent()
        spec = _SUBAGENT_SPECS.get(name)
        if spec is None:
            raise AttributeError(
                f"module {__name__!r} has not initialized {name!r}"
            )
        return spec
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
