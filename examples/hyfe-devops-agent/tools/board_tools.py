"""GitHub Projects v2 board tools implemented via GitHubKit GraphQL.

All tools return JSON-serialized strings and degrade gracefully when
``GITHUB_TOKEN`` is not configured.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from githubkit import GitHub
from langchain_core.tools import tool

_CHECKLIST_RE = re.compile(
    r"^(?P<indent>\s*)[-*]\s*\[(?P<done>[ xX])\]\s*(?P<text>.*)$",
    re.MULTILINE,
)

_IN_PROGRESS_NAMES = {"in progress", "inprogress", "progress", "doing", "wip"}
_IN_SCOPE_NAMES = {
    "in scope",
    "scope",
    "todo",
    "to do",
    "backlog",
    "ready",
    "selected",
    "new",
}


def _token() -> str | None:
    """Return the GitHub token from the environment."""
    return os.getenv("GITHUB_TOKEN")


def _github() -> GitHub | None:
    """Return an authenticated GitHubKit client, or ``None`` if unconfigured."""
    token = _token()
    if not token:
        return None
    return GitHub(token)


def _err(message: str, **kwargs: Any) -> str:
    """Return a JSON-serialized error string."""
    return json.dumps({"error": message, **kwargs}, default=str)


def _check_graphql_errors(result: dict[str, Any]) -> str | None:
    """Return an error string if GraphQL response contains errors."""
    errors = result.get("errors")
    if errors:
        messages = [e.get("message", str(e)) for e in errors]
        return _err("GitHub GraphQL API returned errors.", errors=messages)
    return None


def _parse_checklist(body: str | None) -> list[dict[str, Any]]:
    """Extract markdown checklist items from an issue body.

    Handles ``- [ ]``, ``- [x]``, ``* [ ]``, ``* [x]`` and nested indentation.
    """
    items: list[dict[str, Any]] = []
    if not body:
        return items
    for match in _CHECKLIST_RE.finditer(body):
        indent = match.group("indent")
        text = match.group("text").strip()
        if not text:
            continue
        done = match.group("done").lower() == "x"
        level = len(indent) // 2
        items.append({"text": text, "done": done, "level": level, "indent": len(indent)})
    return items


def _status_category(status_name: str | None) -> str | None:
    """Map a status name to a WIP-limit category, if any."""
    if not status_name:
        return None
    normalized = status_name.lower().strip()
    if normalized in _IN_PROGRESS_NAMES or any(n in normalized for n in _IN_PROGRESS_NAMES):
        return "in_progress"
    if normalized in _IN_SCOPE_NAMES or any(n in normalized for n in _IN_SCOPE_NAMES):
        return "in_scope"
    return None


def _extract_status(field_values: list[dict[str, Any]]) -> str | None:
    """Return the single-select 'Status' value for a project item."""
    for fv in field_values:
        field = fv.get("field") or {}
        field_name = field.get("name", "")
        if field_name and field_name.lower() == "status":
            return fv.get("name")
    return None


def _content_labels(content: dict[str, Any]) -> list[str]:
    """Return label names from an Issue or PullRequest content object."""
    labels = content.get("labels", {}).get("nodes", []) if content else []
    return [lbl["name"] for lbl in labels if lbl.get("name")]


_PROJECT_FIELDS_AND_ITEMS_QUERY = """
query($org: String!, $number: Int!, $first: Int!, $after: String) {
  organization(login: $org) {
    projectV2(number: $number) {
      id
      title
      fields(first: 100) {
        nodes {
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name }
          }
        }
      }
      items(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          fieldValues(first: 30) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2SingleSelectField { name } }
              }
              ... on ProjectV2ItemFieldLabelValue {
                labels(first: 10) { nodes { name } }
              }
            }
          }
          content {
            ... on Issue {
              number
              title
              state
              url
              createdAt
              closedAt
              labels(first: 10) { nodes { name } }
            }
            ... on PullRequest {
              number
              title
              state
              url
              createdAt
              closedAt
              labels(first: 10) { nodes { name } }
            }
          }
        }
      }
    }
  }
}
"""


async def _load_project_state(
    project_number: int,
    org: str,
    include_fields: bool = True,
) -> dict[str, Any] | str:
    """Fetch all project items with pagination and return a structured dict.

    Returns an error string if the token is missing or GraphQL fails.
    """
    gh = _github()
    if gh is None:
        return _err("GITHUB_TOKEN is not set. Configure GITHUB_TOKEN to use board tools.")

    items: list[dict[str, Any]] = []
    project_info: dict[str, Any] = {}
    cursor: str | None = None

    while True:
        result = await gh.async_graphql(
            _PROJECT_FIELDS_AND_ITEMS_QUERY,
            variables={
                "org": org,
                "number": project_number,
                "first": 100,
                "after": cursor,
            },
        )
        err = _check_graphql_errors(result)
        if err:
            return err

        project = result.get("data", {}).get("organization", {}).get("projectV2")
        if not project:
            return _err(f"Project #{project_number} not found in organization '{org}'.")

        project_info.setdefault("id", project.get("id"))
        project_info.setdefault("title", project.get("title"))
        if include_fields:
            project_info["fields"] = project.get("fields", {}).get("nodes", [])

        for node in project.get("items", {}).get("nodes", []):
            content = node.get("content") or {}
            field_values = node.get("fieldValues", {}).get("nodes", [])
            status = _extract_status(field_values)
            labels = _content_labels(content)
            items.append(
                {
                    "item_id": node.get("id"),
                    "content_type": content.get("__typename"),
                    "number": content.get("number"),
                    "title": content.get("title"),
                    "state": content.get("state"),
                    "url": content.get("url"),
                    "created_at": content.get("createdAt"),
                    "closed_at": content.get("closedAt"),
                    "status": status or "Unknown",
                    "labels": labels,
                }
            )

        page_info = project.get("items", {}).get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return {"project": project_info, "items": items}


def _evaluate_wip(
    items: list[dict[str, Any]],
    max_in_scope: int,
    max_in_progress: int,
) -> list[dict[str, Any]]:
    """Return advisory WIP-limit violations for a set of project items."""
    counts: dict[str, int] = {"in_scope": 0, "in_progress": 0}
    for item in items:
        category = _status_category(item.get("status"))
        if category:
            counts[category] += 1

    violations: list[dict[str, Any]] = []
    if counts["in_scope"] > max_in_scope:
        violations.append(
            {
                "category": "in_scope",
                "count": counts["in_scope"],
                "limit": max_in_scope,
                "message": (
                    f"In-scope count ({counts['in_scope']}) exceeds limit "
                    f"({max_in_scope})."
                ),
            }
        )
    if counts["in_progress"] > max_in_progress:
        violations.append(
            {
                "category": "in_progress",
                "count": counts["in_progress"],
                "limit": max_in_progress,
                "message": (
                    f"In-progress count ({counts['in_progress']}) exceeds limit "
                    f"({max_in_progress})."
                ),
            }
        )
    return violations


@tool
async def get_board_state(project_number: int,
*,
org: str,
max_in_scope: int = 10,
max_in_progress: int = 3,) -> str:
    """Return the current state of a GitHub Projects v2 board.

    Args:
        project_number: The GitHub Projects v2 project number.
        org: GitHub organization login that owns the project.
        max_in_scope: Advisory WIP limit for in-scope/todo/backlog items.
        max_in_progress: Advisory WIP limit for in-progress items.

    Returns:
        JSON string with items grouped by status, counts, and WIP violations.
    """
    state = await _load_project_state(project_number, org)
    if isinstance(state, str):
        return state

    items = state["items"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["status"], []).append(item)

    counts = {status: len(group) for status, group in grouped.items()}
    violations = _evaluate_wip(items, max_in_scope, max_in_progress)

    return json.dumps(
        {
            "project_id": state["project"].get("id"),
            "project_title": state["project"].get("title"),
            "total_items": len(items),
            "items_by_status": grouped,
            "counts": counts,
            "wip_limits": {
                "max_in_scope": max_in_scope,
                "max_in_progress": max_in_progress,
            },
            "wip_violations": violations,
            "advisory_only": True,
        },
        default=str,
    )


@tool
async def check_wip_limits(*,
project_number: int,
org: str,
max_in_scope: int = 10,
max_in_progress: int = 3,) -> str:
    """Check whether a GitHub Projects v2 board exceeds configured WIP limits.

    Args:
        project_number: The GitHub Projects v2 project number.
        org: GitHub organization login that owns the project.
        max_in_scope: Advisory WIP limit for in-scope/todo/backlog items.
        max_in_progress: Advisory WIP limit for in-progress items.

    Returns:
        JSON string with counts and any WIP-limit violations (advisory only).
    """
    state = await _load_project_state(project_number, org)
    if isinstance(state, str):
        return state

    items = state["items"]
    violations = _evaluate_wip(items, max_in_scope, max_in_progress)

    return json.dumps(
        {
            "project_number": project_number,
            "org": org,
            "total_items": len(items),
            "in_scope_count": sum(
                1 for i in items if _status_category(i.get("status")) == "in_scope"
            ),
            "in_progress_count": sum(
                1 for i in items if _status_category(i.get("status")) == "in_progress"
            ),
            "wip_limits": {
                "max_in_scope": max_in_scope,
                "max_in_progress": max_in_progress,
            },
            "violations": violations,
            "advisory_only": True,
        },
        default=str,
    )


@tool
async def list_blocked_tickets(*, project_number: int, org: str) -> str:
    """Return project items that appear blocked by status or label.

    Args:
        project_number: The GitHub Projects v2 project number.
        org: GitHub organization login that owns the project.

    Returns:
        JSON string listing blocked items with status, labels, and URLs.
    """
    state = await _load_project_state(project_number, org)
    if isinstance(state, str):
        return state

    blocked: list[dict[str, Any]] = []
    for item in state["items"]:
        status = (item.get("status") or "").lower()
        labels = {lbl.lower() for lbl in item.get("labels", [])}
        title = (item.get("title") or "").lower()
        if (
            status == "blocked"
            or "blocked" in labels
            or title.startswith("blocked:")
        ):
            blocked.append(item)

    return json.dumps(
        {
            "project_number": project_number,
            "org": org,
            "blocked_count": len(blocked),
            "blocked_items": blocked,
        },
        default=str,
    )


_MOVE_TICKET_QUERIES = {
    "issue": """
query($org: String!, $repo: String!, $number: Int!) {
  repository(owner: $org, name: $repo) {
    id
    issue(number: $number) { id title number }
  }
}
""",
    "project_fields": """
query($org: String!, $number: Int!) {
  organization(login: $org) {
    projectV2(number: $number) {
      id
      fields(first: 100) {
        nodes {
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name }
          }
        }
      }
    }
  }
}
""",
    "add_item": """
mutation($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
    item { id }
  }
}
""",
    "update_field": """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(
    input: {
      projectId: $projectId,
      itemId: $itemId,
      fieldId: $fieldId,
      value: {singleSelectOptionId: $optionId}
    }
  ) {
    projectV2Item { id }
  }
}
""",
}


@tool
async def move_ticket(issue_number: int,
target_status: str,
*,
project_number: int,
org: str,
repo: str,) -> str:
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
    gh = _github()
    if gh is None:
        return _err("GITHUB_TOKEN is not set. Configure GITHUB_TOKEN to move tickets.")

    issue_result = await gh.async_graphql(
        _MOVE_TICKET_QUERIES["issue"],
        variables={"org": org, "repo": repo, "number": issue_number},
    )
    err = _check_graphql_errors(issue_result)
    if err:
        return err

    repo_data = issue_result.get("data", {}).get("repository", {})
    issue = repo_data.get("issue")
    if not issue:
        return _err(f"Issue #{issue_number} not found in {org}/{repo}.")
    issue_id = issue["id"]

    fields_result = await gh.async_graphql(
        _MOVE_TICKET_QUERIES["project_fields"],
        variables={"org": org, "number": project_number},
    )
    err = _check_graphql_errors(fields_result)
    if err:
        return err

    project = fields_result.get("data", {}).get("organization", {}).get("projectV2")
    if not project:
        return _err(f"Project #{project_number} not found in organization '{org}'.")

    project_id = project["id"]
    status_field = None
    target_option_id = None
    for field in project.get("fields", {}).get("nodes", []):
        if (field.get("name") or "").lower() == "status":
            status_field = field
            for option in field.get("options", []):
                if (option.get("name") or "").lower() == target_status.lower():
                    target_option_id = option["id"]
                    break
            break

    if not status_field:
        return _err(f"No Status field found on project #{project_number}.")
    if not target_option_id:
        return _err(
            f"Status '{target_status}' not found on project #{project_number}."
        )

    state = await _load_project_state(project_number, org, include_fields=False)
    if isinstance(state, str):
        return state

    item_id: str | None = None
    current_status: str | None = None
    for item in state["items"]:
        if item.get("number") == issue_number:
            item_id = item.get("item_id")
            current_status = item.get("status")
            break

    if item_id is None:
        add_result = await gh.async_graphql(
            _MOVE_TICKET_QUERIES["add_item"],
            variables={"projectId": project_id, "contentId": issue_id},
        )
        err = _check_graphql_errors(add_result)
        if err:
            return err
        item_id = (
            add_result.get("data", {})
            .get("addProjectV2ItemById", {})
            .get("item", {})
            .get("id")
        )
        if not item_id:
            return _err("Failed to add issue to project.")

    if current_status and current_status.lower() == target_status.lower():
        return json.dumps(
            {
                "success": True,
                "issue_number": issue_number,
                "target_status": target_status,
                "project_number": project_number,
                "item_id": item_id,
                "noop": True,
                "message": f"Issue #{issue_number} is already '{target_status}'.",
            },
            default=str,
        )

    update_result = await gh.async_graphql(
        _MOVE_TICKET_QUERIES["update_field"],
        variables={
            "projectId": project_id,
            "itemId": item_id,
            "fieldId": status_field["id"],
            "optionId": target_option_id,
        },
    )
    err = _check_graphql_errors(update_result)
    if err:
        return err

    return json.dumps(
        {
            "success": True,
            "issue_number": issue_number,
            "target_status": target_status,
            "project_number": project_number,
            "item_id": item_id,
            "noop": False,
        },
        default=str,
    )


_ISSUE_BODY_QUERY = """
query($org: String!, $repo: String!, $number: Int!) {
  repository(owner: $org, name: $repo) {
    issue(number: $number) {
      id
      body
      subIssues(first: 100) {
        nodes {
          number
          title
          state
          url
        }
      }
    }
  }
}
"""


@tool
async def parse_subtasks(issue_number: int, *, org: str, repo: str) -> str:
    """Parse checklist subtasks and linked sub-issues for a GitHub issue.

    Args:
        issue_number: Issue number to inspect.
        org: GitHub organization login that owns the repository.
        repo: Repository name containing the issue.

    Returns:
        JSON string with checklist completion stats and linked sub-issues.
    """
    gh = _github()
    if gh is None:
        return _err("GITHUB_TOKEN is not set. Configure GITHUB_TOKEN to parse subtasks.")

    result = await gh.async_graphql(
        _ISSUE_BODY_QUERY,
        variables={"org": org, "repo": repo, "number": issue_number},
    )
    err = _check_graphql_errors(result)
    if err:
        return err

    issue = result.get("data", {}).get("repository", {}).get("issue")
    if not issue:
        return _err(f"Issue #{issue_number} not found in {org}/{repo}.")

    checklist_items = _parse_checklist(issue.get("body") or "")
    completed = sum(1 for item in checklist_items if item["done"])
    total = len(checklist_items)
    sub_issues = [
        {
            "number": s.get("number"),
            "title": s.get("title"),
            "state": s.get("state"),
            "url": s.get("url"),
        }
        for s in issue.get("subIssues", {}).get("nodes", [])
    ]

    return json.dumps(
        {
            "issue_number": issue_number,
            "org": org,
            "repo": repo,
            "total": total,
            "completed": completed,
            "incomplete": total - completed,
            "completion_rate": round(completed / total, 2) if total else 1.0,
            "checklist_items": checklist_items,
            "sub_issues": sub_issues,
            "sub_issue_count": len(sub_issues),
        },
        default=str,
    )


_CREATE_SUBTASK_QUERIES = {
    "repo_and_parent": """
query($org: String!, $repo: String!, $number: Int!) {
  repository(owner: $org, name: $repo) {
    id
    issue(number: $number) { id number }
  }
}
""",
    "create_issue": """
mutation($repoId: ID!, $title: String!, $body: String) {
  createIssue(input: {repositoryId: $repoId, title: $title, body: $body}) {
    issue { id number url }
  }
}
""",
    "add_sub_issue": """
mutation($parentId: ID!, $subIssueId: ID!) {
  addSubIssue(input: {parentIssueId: $parentId, subIssueId: $subIssueId}) {
    subIssue { number url }
  }
}
""",
    "update_issue": """
mutation($issueId: ID!, $body: String!) {
  updateIssue(input: {id: $issueId, body: $body}) {
    issue { number url }
  }
}
""",
}


@tool
async def create_subtask(parent_issue_number: int,
title: str,
*,
org: str,
repo: str,
body: str = "",) -> str:
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
    gh = _github()
    if gh is None:
        return _err("GITHUB_TOKEN is not set. Configure GITHUB_TOKEN to create subtasks.")

    parent_result = await gh.async_graphql(
        _CREATE_SUBTASK_QUERIES["repo_and_parent"],
        variables={"org": org, "repo": repo, "number": parent_issue_number},
    )
    err = _check_graphql_errors(parent_result)
    if err:
        return err

    repo_data = parent_result.get("data", {}).get("repository", {})
    parent = repo_data.get("issue")
    repo_id = repo_data.get("id")
    if not parent or not repo_id:
        return _err(f"Parent issue #{parent_issue_number} not found in {org}/{repo}.")
    parent_id = parent["id"]

    create_result = await gh.async_graphql(
        _CREATE_SUBTASK_QUERIES["create_issue"],
        variables={"repoId": repo_id, "title": title, "body": body or None},
    )
    err = _check_graphql_errors(create_result)
    if err:
        return err

    created = create_result.get("data", {}).get("createIssue", {}).get("issue")
    if not created:
        return _err("Failed to create subtask issue.")
    created_id = created["id"]
    created_number = created["number"]
    created_url = created["url"]

    linked = False
    link_result = await gh.async_graphql(
        _CREATE_SUBTASK_QUERIES["add_sub_issue"],
        variables={"parentId": parent_id, "subIssueId": created_id},
    )
    link_err = _check_graphql_errors(link_result)
    if not link_err:
        linked = True
    else:
        err_text = link_err.lower()
        if "addsubissue" not in err_text and "add_sub_issue" not in err_text:
            return link_err
        fallback_body = (body or "") + f"\n\nPart of #{parent_issue_number}"
        update_result = await gh.async_graphql(
            _CREATE_SUBTASK_QUERIES["update_issue"],
            variables={"issueId": created_id, "body": fallback_body.strip()},
        )
        err = _check_graphql_errors(update_result)
        if err:
            return err

    return json.dumps(
        {
            "success": True,
            "parent_issue_number": parent_issue_number,
            "subtask_issue_number": created_number,
            "url": created_url,
            "linked": linked,
        },
        default=str,
    )


_SPRINT_VELOCITY_QUERY = """
query($org: String!, $number: Int!, $first: Int!, $after: String) {
  organization(login: $org) {
    projectV2(number: $number) {
      id
      title
      items(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          content {
            ... on Issue {
              number
              title
              state
              url
              createdAt
              closedAt
              body
            }
            ... on PullRequest {
              number
              title
              state
              url
              createdAt
              closedAt
              body
            }
          }
        }
      }
    }
  }
}
"""


def _sprint_window(end_date: datetime, days: int = 14) -> tuple[datetime, datetime]:
    """Return a sprint window ending on ``end_date`` (inclusive)."""
    start = end_date - timedelta(days=days - 1)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


def _parse_github_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp from GitHub GraphQL."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@tool
async def get_sprint_velocity(*,
org: str,
repo: str,
project_number: int,
sprint_count: int = 3,) -> str:
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
    gh = _github()
    if gh is None:
        return _err("GITHUB_TOKEN is not set. Configure GITHUB_TOKEN for velocity.")

    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        result = await gh.async_graphql(
            _SPRINT_VELOCITY_QUERY,
            variables={"org": org, "number": project_number, "first": 100, "after": cursor},
        )
        err = _check_graphql_errors(result)
        if err:
            return err

        project = result.get("data", {}).get("organization", {}).get("projectV2")
        if not project:
            return _err(f"Project #{project_number} not found in organization '{org}'.")

        for node in project.get("items", {}).get("nodes", []):
            content = node.get("content") or {}
            if content.get("__typename") in {"Issue", "PullRequest"}:
                items.append(
                    {
                        "number": content.get("number"),
                        "title": content.get("title"),
                        "state": content.get("state"),
                        "url": content.get("url"),
                        "created_at": _parse_github_timestamp(content.get("createdAt")),
                        "closed_at": _parse_github_timestamp(content.get("closedAt")),
                        "body": content.get("body") or "",
                    }
                )

        page_info = project.get("items", {}).get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    today = datetime.now(timezone.utc)
    sprints: list[dict[str, Any]] = []
    closed_per_sprint: list[int] = []

    for i in range(sprint_count):
        end = today - timedelta(days=i * 14)
        start, end = _sprint_window(end)
        name = f"Sprint {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"

        sprint_items = [
            item
            for item in items
            if item["closed_at"] and start <= item["closed_at"] <= end
        ]
        total_issues = len(sprint_items)
        closed_issues = sum(1 for item in sprint_items if item["state"] == "CLOSED")

        completion_rates: list[float] = []
        cycle_times: list[float] = []
        for item in sprint_items:
            checklist = _parse_checklist(item["body"])
            total = len(checklist)
            done = sum(1 for c in checklist if c["done"])
            completion_rates.append(round(done / total, 2) if total else 1.0)
            if item["created_at"] and item["closed_at"]:
                cycle_times.append(
                    (item["closed_at"] - item["created_at"]).total_seconds() / 86400
                )

        avg_completion = round(sum(completion_rates) / len(completion_rates), 2) if completion_rates else 1.0
        avg_cycle = round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else 0.0

        sprints.append(
            {
                "name": name,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "total_issues": total_issues,
                "closed_issues": closed_issues,
                "avg_subtask_completion": avg_completion,
                "avg_cycle_time_days": avg_cycle,
            }
        )
        closed_per_sprint.append(closed_issues)

    overall_velocity = round(sum(closed_per_sprint) / len(closed_per_sprint), 2) if closed_per_sprint else 0.0
    trend = "stable"
    if len(closed_per_sprint) >= 2:
        if closed_per_sprint[0] > closed_per_sprint[1]:
            trend = "increasing"
        elif closed_per_sprint[0] < closed_per_sprint[1]:
            trend = "decreasing"

    return json.dumps(
        {
            "org": org,
            "repo": repo,
            "project_number": project_number,
            "sprint_count": sprint_count,
            "sprints": sprints,
            "overall_velocity": overall_velocity,
            "trend": trend,
        },
        default=str,
    )
