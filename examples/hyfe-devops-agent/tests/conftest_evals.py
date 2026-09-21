"""DeepEval evaluation fixtures for the Hyfe DevOps agent.

Provides CallbackHandler and metric fixtures for integration evals.
All metrics run locally — no DEEPEVAL_API_KEY required.
No tracing vendor dependencies.
"""

import os

import pytest
from deepeval.integrations.langchain import CallbackHandler
from deepeval.metrics import TaskCompletionMetric, ToolCorrectnessMetric


@pytest.fixture
def deepeval_callback() -> CallbackHandler:
    return CallbackHandler()


@pytest.fixture
def tool_correctness_metric() -> ToolCorrectnessMetric:
    threshold = float(os.environ.get("TOOL_CORRECTNESS_THRESHOLD", "0.7"))
    return ToolCorrectnessMetric(should_exact_match=False, threshold=threshold)


@pytest.fixture
def task_completion_metric() -> TaskCompletionMetric:
    threshold = float(os.environ.get("TASK_COMPLETION_THRESHOLD", "0.7"))
    return TaskCompletionMetric(threshold=threshold)


@pytest.fixture
def eval_metrics(tool_correctness_metric, task_completion_metric) -> list:
    return [tool_correctness_metric, task_completion_metric]
