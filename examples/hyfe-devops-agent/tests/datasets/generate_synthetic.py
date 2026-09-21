#!/usr/bin/env python3
"""Generate synthetic Golden test cases for the Hyfe DevOps agent using DeepEval Synthesizer.

Usage:
    uv run --group test python tests/datasets/generate_synthetic.py [--force]

Saves goldens to tests/datasets/synthetic_goldens.json.
Idempotent — skips if dataset exists unless --force is passed.
"""

import argparse
import json
import os
import sys
from typing import Optional, Union

from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.synthesizer import Synthesizer
from deepeval.synthesizer.config import EvolutionConfig
from deepeval.synthesizer.schema import InputFeedback, Response, SyntheticDataList
from pydantic import BaseModel


DATASET_PATH = os.path.join(os.path.dirname(__file__), "synthetic_goldens.json")
SOURCE_DOCS = [
    os.path.join(os.path.dirname(__file__), "..", "..", "AGENTS.md"),
    os.path.join(os.path.dirname(__file__), "..", "..", "README.md"),
]

VALID_TOOL_NAMES = {
    "get_board_state",
    "check_wip_limits",
    "list_blocked_tickets",
    "move_ticket",
    "parse_subtasks",
    "create_subtask",
    "get_sprint_velocity",
    "send_notification",
    "post_sprint_summary",
    "create_slack_vote",
    "evaluate_slack_vote",
}

SCENARIOS = [
    {
        "context": (
            "The Hyfe DevOps orchestrator tracks sprint execution and board health. "
            "It uses get_board_state to retrieve the current board, check_wip_limits "
            "to detect advisory WIP violations, and list_blocked_tickets to surface "
            "blockers. The sprint-coordinator subagent analyzes these results."
        ),
        "input": "What's the current sprint status and are there WIP limit violations?",
        "output": (
            "The sprint has 5 items in progress, no WIP limit violations, and one "
            "blocked ticket awaiting infrastructure approval."
        ),
    },
    {
        "context": (
            "Blocked ticket triage starts with list_blocked_tickets to identify "
            "project items that appear blocked by status, label, or title prefix. "
            "The sprint-coordinator subagent then proposes next steps to unblock them."
        ),
        "input": "Which tickets are blocked and what needs to happen to unblock them?",
        "output": (
            "Issue #43 'Blocked: roll out new ingress' is blocked on platform-team "
            "approval; escalate to the platform team and update the ticket once unblocked."
        ),
    },
    {
        "context": (
            "Release readiness is assessed by the release-manager subagent using "
            "get_board_state, parse_subtasks to check checklist completion, and "
            "send_notification to alert the team about blocking issues or open PRs."
        ),
        "input": "Is the release ready? Check for blocking issues and open PRs.",
        "output": (
            "The release is not ready because PR #42 is still open and issue #43 is "
            "blocked; notify the team and revisit once the blockers are cleared."
        ),
    },
    {
        "context": (
            "The orchestrator can post summaries and polls to Slack. "
            "create_slack_vote creates an emoji-based poll, and evaluate_slack_vote "
            "counts votes per option with tie detection."
        ),
        "input": "Create a Slack vote to decide whether we are ready to release.",
        "output": (
            "A Slack vote was created in the default channel with options: "
            "Yes, No, Needs more testing. The vote is open for reactions."
        ),
    },
]

TOOL_HINTS = {
    "sprint status": ["get_board_state", "check_wip_limits"],
    "wip limit": ["check_wip_limits"],
    "blocked": ["list_blocked_tickets"],
    "release ready": ["get_board_state", "parse_subtasks", "send_notification"],
    "vote": ["create_slack_vote", "evaluate_slack_vote"],
    "slack vote": ["create_slack_vote", "evaluate_slack_vote"],
}


class LocalDevOpsLLM(DeepEvalBaseLLM):
    def __init__(self) -> None:
        super().__init__("local-devops-llm")
        self._input_index = 0
        self._output_index = 0

    def load_model(self) -> "LocalDevOpsLLM":
        return self

    def generate(
        self, prompt: str, schema: Optional[BaseModel] = None
    ) -> Union[str, BaseModel]:
        if schema is SyntheticDataList:
            idx = self._input_index % len(SCENARIOS)
            self._input_index += 1
            return SyntheticDataList(data=[{"input": SCENARIOS[idx]["input"]}])
        if schema is Response:
            idx = self._output_index % len(SCENARIOS)
            self._output_index += 1
            return Response(response=SCENARIOS[idx]["output"])
        if schema is InputFeedback:
            return InputFeedback(score=1.0, feedback="Clear and answerable.")
        return "ok"

    async def a_generate(
        self, prompt: str, schema: Optional[BaseModel] = None
    ) -> Union[str, BaseModel]:
        return self.generate(prompt, schema)

    def get_model_name(self) -> str:
        return "local-devops-llm"


def _extract_contexts() -> list[list[str]]:
    contexts = []
    for path in SOURCE_DOCS:
        with open(path) as f:
            text = f.read()
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        for paragraph in paragraphs[:2]:
            contexts.append([paragraph])
    return contexts


def _infer_tools(query: str) -> list[str]:
    lowered = query.lower()
    tools: set[str] = set()
    for hint, tool_names in TOOL_HINTS.items():
        if hint in lowered:
            tools.update(tool_names)
    return sorted(tools)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic test dataset")
    parser.add_argument(
        "--force", action="store_true", help="Overwrite existing dataset"
    )
    args = parser.parse_args()

    if os.path.exists(DATASET_PATH) and not args.force:
        print(f"Dataset already exists at {DATASET_PATH}. Use --force to regenerate.")
        sys.exit(0)

    print("Generating synthetic golden test cases...")
    synthesizer = Synthesizer(
        model=LocalDevOpsLLM(),
        async_mode=False,
        evolution_config=EvolutionConfig(num_evolutions=0),
    )

    contexts = _extract_contexts()
    goldens = synthesizer.generate_goldens_from_contexts(
        contexts=contexts,
        include_expected_output=True,
        max_goldens_per_context=1,
    )

    serialized = []
    for i, g in enumerate(goldens):
        scenario = SCENARIOS[i % len(SCENARIOS)]
        serialized.append(
            {
                "input": g.input,
                "expected_output": g.expected_output,
                "expected_tools": _infer_tools(scenario["input"]),
            }
        )

    with open(DATASET_PATH, "w") as f:
        json.dump(serialized, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(serialized)} goldens → {DATASET_PATH}")
    for entry in serialized:
        print(f"  - {entry['input'][:80]}...")


if __name__ == "__main__":
    main()
