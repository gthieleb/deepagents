"""Slack notification and emoji voting tools.

All Slack Web API calls use ``httpx.AsyncClient``. Authentication is via the
``SLACK_BOT_TOKEN`` environment variable. Tools return JSON-serialized strings.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from langchain_core.tools import tool

_SLACK_BASE_URL = "https://slack.com/api/"

# Slack-compatible emoji shortcodes (without colons) used for voting.
# The first 8 are colored circles, followed by digit keycaps, then common
# symbols. This gives 23 unique reaction emojis, matching Slack's per-message
# reaction limit.
_EMOJI_PALETTE: list[str] = [
    "red_circle",
    "large_yellow_circle",
    "large_green_circle",
    "large_blue_circle",
    "large_purple_circle",
    "black_circle",
    "white_circle",
    "large_brown_circle",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "keycap_ten",
    "star",
    "heart",
    "thumbsup",
    "thumbsdown",
    "rocket",
]

_EMOJI_CHAR: dict[str, str] = {
    "red_circle": "🔴",
    "large_yellow_circle": "🟡",
    "large_green_circle": "🟢",
    "large_blue_circle": "🔵",
    "large_purple_circle": "🟣",
    "black_circle": "⚫",
    "white_circle": "⚪",
    "large_brown_circle": "🟤",
    "one": "1️⃣",
    "two": "2️⃣",
    "three": "3️⃣",
    "four": "4️⃣",
    "five": "5️⃣",
    "six": "6️⃣",
    "seven": "7️⃣",
    "eight": "8️⃣",
    "nine": "9️⃣",
    "keycap_ten": "🔟",
    "star": "⭐",
    "heart": "❤️",
    "thumbsup": "👍",
    "thumbsdown": "👎",
    "rocket": "🚀",
}

_MAX_OPTIONS = len(_EMOJI_PALETTE)


def _error_response(message: str) -> str:
    """Return a JSON-serialized error payload."""
    return json.dumps({"ok": False, "error": message})


def _check_token() -> tuple[str | None, str | None]:
    """Validate that ``SLACK_BOT_TOKEN`` is configured."""
    token = os.getenv("SLACK_BOT_TOKEN")
    if token:
        return token, None
    return None, _error_response(
        "SLACK_BOT_TOKEN environment variable is not set. Configure "
        "SLACK_BOT_TOKEN to use Slack tools."
    )


def _resolve_channel(channel: str) -> str:
    """Use the provided channel or fall back to ``SLACK_DEFAULT_CHANNEL``."""
    if channel:
        return channel
    return os.getenv("SLACK_DEFAULT_CHANNEL", "")


def _slack_client(token: str) -> httpx.AsyncClient:
    """Create an ``httpx.AsyncClient`` configured for the Slack Web API."""
    return httpx.AsyncClient(
        base_url=_SLACK_BASE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        timeout=30.0,
    )


def _slack_api_error(data: dict[str, Any]) -> str:
    """Format a Slack ``ok: false`` response."""
    error = data.get("error") or "unknown_slack_error"
    return _error_response(f"Slack API error: {error}")


async def _bot_user_id(client: httpx.AsyncClient) -> str | None:
    """Return the bot's own user ID via ``auth.test``."""
    try:
        response = await client.post("auth.test")
    except httpx.HTTPError:
        return None
    data = response.json()
    if data.get("ok"):
        return data.get("user_id")
    return None


@tool
async def send_notification(channel: str, message: str) -> str:
    """Post a plain-text notification to a Slack channel.

    Args:
        channel: Slack channel ID or name to post in. Falls back to
            ``SLACK_DEFAULT_CHANNEL`` if empty.
        message: Plain-text message body.

    Returns:
        JSON-serialized result with ``ok``, ``channel``, and ``ts`` fields,
        or an error object.
    """
    token, error = _check_token()
    if token is None:
        return error

    channel = _resolve_channel(channel)
    if not channel:
        return _error_response("channel is required (or set SLACK_DEFAULT_CHANNEL).")

    async with _slack_client(token) as client:
        try:
            response = await client.post(
                "chat.postMessage",
                json={"channel": channel, "text": message},
            )
        except httpx.HTTPError as exc:
            return json.dumps({"error": f"Slack API request failed: {exc}"})
        data = response.json()

    if not data.get("ok"):
        return _slack_api_error(data)

    return json.dumps(
        {
            "ok": True,
            "channel": data.get("channel"),
            "ts": data.get("ts"),
            "message": "Notification sent.",
        }
    )


@tool
async def post_sprint_summary(*, channel: str, summary: str) -> str:
    """Post a formatted sprint summary to a Slack channel.

    Args:
        channel: Slack channel ID or name to post in. Falls back to
            ``SLACK_DEFAULT_CHANNEL`` if empty.
        summary: Markdown sprint summary text.

    Returns:
        JSON-serialized result with ``ok``, ``channel``, and ``ts`` fields,
        or an error object.
    """
    token, error = _check_token()
    if token is None:
        return error

    channel = _resolve_channel(channel)
    if not channel:
        return _error_response("channel is required (or set SLACK_DEFAULT_CHANNEL).")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Sprint Summary", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary},
        },
    ]

    async with _slack_client(token) as client:
        try:
            response = await client.post(
                "chat.postMessage",
                json={
                    "channel": channel,
                    "text": summary,
                    "blocks": blocks,
                },
            )
        except httpx.HTTPError as exc:
            return json.dumps({"error": f"Slack API request failed: {exc}"})
        data = response.json()

    if not data.get("ok"):
        return _slack_api_error(data)

    return json.dumps(
        {
            "ok": True,
            "channel": data.get("channel"),
            "ts": data.get("ts"),
            "message": "Sprint summary posted.",
        }
    )


@tool
async def create_slack_vote(question: str, options: list[str], *, channel: str) -> str:
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
    token, error = _check_token()
    if token is None:
        return error

    channel = _resolve_channel(channel)
    if not channel:
        return _error_response("channel is required (or set SLACK_DEFAULT_CHANNEL).")

    if len(options) > _MAX_OPTIONS:
        return _error_response(
            f"Too many options: {len(options)} (maximum {_MAX_OPTIONS})."
        )

    emoji_mapping = {option: _EMOJI_PALETTE[i] for i, option in enumerate(options)}

    lines = [f"*{question}*", ""]
    for option, emoji_name in emoji_mapping.items():
        lines.append(f"{_EMOJI_CHAR.get(emoji_name, '')} {option}")
    text = "\n".join(lines)

    async with _slack_client(token) as client:
        try:
            post_response = await client.post(
                "chat.postMessage",
                json={"channel": channel, "text": text},
            )
        except httpx.HTTPError as exc:
            return json.dumps({"error": f"Slack API request failed: {exc}"})
        post_data = post_response.json()

        if not post_data.get("ok"):
            return _slack_api_error(post_data)

        timestamp = post_data.get("ts")
        reaction_errors: list[str] = []

        for emoji_name in emoji_mapping.values():
            try:
                reaction_response = await client.post(
                    "reactions.add",
                    json={
                        "channel": post_data.get("channel", channel),
                        "timestamp": timestamp,
                        "name": emoji_name,
                    },
                )
            except httpx.HTTPError as exc:
                return json.dumps({"error": f"Slack API request failed: {exc}"})
            reaction_data = reaction_response.json()
            if not reaction_data.get("ok"):
                err = reaction_data.get("error", "unknown")
                # A duplicate reaction from the bot is unexpected but harmless.
                if err != "already_reacted":
                    reaction_errors.append(f"{emoji_name}: {err}")

    result: dict[str, Any] = {
        "ok": len(reaction_errors) == 0,
        "channel": post_data.get("channel"),
        "ts": timestamp,
        "question": question,
        "options": options,
        "emoji_mapping": emoji_mapping,
    }
    if reaction_errors:
        result["ok"] = False
        result["error"] = "Failed to add some reactions: " + ", ".join(reaction_errors)

    return json.dumps(result)


@tool
async def evaluate_slack_vote(
    channel: str, message_ts: str, *, options: list[str]
) -> str:
    """Evaluate an emoji-based poll previously created by ``create_slack_vote``.

    Reads reactions on the message, counts votes per option, and detects ties.
    A user who reacted to multiple option emojis is counted only for the first
    option in the provided ``options`` order. Non-option emojis and the bot's
    own reactions are excluded.

    Args:
        channel: Slack channel ID where the vote message was posted.
        message_ts: Timestamp ``ts`` of the vote message.
        options: The same option list passed to ``create_slack_vote``.

    Returns:
        JSON-serialized result with ``ok``, ``counts``, ``winners``, ``tie``,
        and ``total_voters``, or an error object.
    """
    token, error = _check_token()
    if token is None:
        return error

    if len(options) > _MAX_OPTIONS:
        return _error_response(
            f"Too many options: {len(options)} (maximum {_MAX_OPTIONS})."
        )

    option_emojis = [_EMOJI_PALETTE[i] for i in range(len(options))]

    async with _slack_client(token) as client:
        bot_id = await _bot_user_id(client)

        try:
            response = await client.get(
                "reactions.get",
                params={
                    "channel": channel,
                    "timestamp": message_ts,
                    "full": "true",
                },
            )
        except httpx.HTTPError as exc:
            return json.dumps({"error": f"Slack API request failed: {exc}"})
        data = response.json()

    if not data.get("ok"):
        return _slack_api_error(data)

    message = data.get("message") or {}
    reactions = message.get("reactions") or []

    counts = [0] * len(options)
    assigned_voters: set[str] = set()
    anonymous_votes = [0] * len(options)

    # Process options in order so a multi-reacting user is counted for the
    # earliest option they voted for.
    for option_idx, emoji_name in enumerate(option_emojis):
        reaction = next(
            (r for r in reactions if r.get("name") == emoji_name),
            None,
        )
        if reaction is None:
            continue

        total_count = max(reaction.get("count", 0) - 1, 0)
        users = reaction.get("users") or []
        non_bot_users = [u for u in users if u != bot_id]

        for user_id in non_bot_users:
            if user_id not in assigned_voters:
                assigned_voters.add(user_id)
                counts[option_idx] += 1

        # Slack may truncate the users list for heavily-reacted messages. The
        # remaining votes are counted anonymously for this option.
        accounted = len(non_bot_users)
        if total_count > accounted:
            anonymous_votes[option_idx] += total_count - accounted

    for i, extra in enumerate(anonymous_votes):
        counts[i] += extra

    count_dict = {option: counts[i] for i, option in enumerate(options)}

    max_votes = max(counts) if counts else 0
    if max_votes > 0:
        winners = [options[i] for i, c in enumerate(counts) if c == max_votes]
    else:
        winners = []

    total_voters = sum(counts)

    return json.dumps(
        {
            "ok": True,
            "channel": channel,
            "ts": message_ts,
            "counts": count_dict,
            "winners": winners,
            "tie": len(winners) > 1,
            "total_voters": total_voters,
        }
    )
