import json
import os

DATASET_PATH = os.path.join(os.path.dirname(__file__), "synthetic_goldens.json")

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


def test_goldens_exist() -> None:
    assert os.path.exists(DATASET_PATH), f"Dataset not found at {DATASET_PATH}"


def test_goldens_have_required_fields() -> None:
    with open(DATASET_PATH) as f:
        goldens = json.load(f)
    assert len(goldens) >= 3, f"Expected ≥3 goldens, got {len(goldens)}"
    for i, g in enumerate(goldens):
        assert "input" in g, f"Golden {i} missing 'input'"
        assert "expected_output" in g, f"Golden {i} missing 'expected_output'"
        assert "expected_tools" in g, f"Golden {i} missing 'expected_tools'"
        assert g["input"], f"Golden {i} has empty 'input'"
        assert g["expected_output"], f"Golden {i} has empty 'expected_output'"
        assert isinstance(g["expected_tools"], list), (
            f"Golden {i} 'expected_tools' is not a list"
        )


def test_expected_tools_match_stubs() -> None:
    with open(DATASET_PATH) as f:
        goldens = json.load(f)
    for i, g in enumerate(goldens):
        invalid = [t for t in g.get("expected_tools", []) if t not in VALID_TOOL_NAMES]
        assert not invalid, f"Golden {i} has invalid tools: {invalid}"
