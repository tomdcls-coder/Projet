"""GitHub pull-request catchup digest posted to the user's Slack self-chat."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import mistralai.workflows as workflows
import pydantic
from mistralai.workflows import Depends
from mistralai.workflows.plugins.mistralai.connectors import (
    ToolCallClient,
    connector,
    uses_connectors,
)

github_connector = connector("github_app")
slack_connector = connector("slack")

DEFAULT_MAX_PER_SECTION = 20
PER_PAGE = 100
MAX_PAGES = 3
MAX_TITLE_LENGTH = 120

PR_FIELDS = [
    "number",
    "title",
    "state",
    "html_url",
    "user",
    "created_at",
    "closed_at",
]


class RepoCatchupInput(pydantic.BaseModel):
    repo: str
    since: str
    slack_user_id: str | None = None
    max_per_section: int = DEFAULT_MAX_PER_SECTION


class RepoCatchupResult(pydantic.BaseModel):
    repo: str
    since: str
    merged_count: int
    pending_review_count: int
    slack_channel_id: str | None
    digest: str


def validate_repo(repo: str) -> str:
    repo = repo.strip().removeprefix("https://github.com/").strip("/")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repo):
        raise ValueError(
            f"Invalid repository {repo!r}: expected 'owner/name' or a github.com URL"
        )
    return repo


def validate_since(since: str) -> str:
    try:
        parsed = datetime.strptime(since.strip(), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date {since!r}: expected 'YYYY-MM-DD'") from exc
    return parsed.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d")


def _extract_tool_payload(result: Any) -> Any:
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except ValueError:
                    return result
    return result


def _extract_prs(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _one_line_summary(pr: dict[str, Any], date_field: str) -> str:
    number = pr.get("number", "?")
    title = str(pr.get("title") or "untitled").replace("\n", " ").strip()
    if len(title) > MAX_TITLE_LENGTH:
        title = title[: MAX_TITLE_LENGTH - 3] + "..."
    author = (pr.get("user") or {}).get("login") or "unknown"
    url = pr.get("html_url") or ""
    date = str(pr.get(date_field) or "")[:10]
    label = f"#{number} {title}" if url else f"#{number} {title} (sans lien)"
    link = f"<{url}|{label}>" if url else label
    return f"- {link} — _{author}_ ({date})"


def build_digest(
    repo: str,
    since: str,
    merged: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    max_per_section: int = DEFAULT_MAX_PER_SECTION,
) -> str:
    lines = [f"*Rattrapage GitHub — `{repo}` (depuis {since})*", ""]

    lines.append(f"*Fusionnées depuis le {since}* ({len(merged)})")
    if merged:
        for pr in merged[:max_per_section]:
            lines.append(_one_line_summary(pr, "closed_at"))
        if len(merged) > max_per_section:
            lines.append(f"- _…et {len(merged) - max_per_section} de plus_")
    else:
        lines.append("- _aucune_")
    lines.append("")

    lines.append(f"*En attente de revue* ({len(pending)})")
    if pending:
        for pr in pending[:max_per_section]:
            lines.append(_one_line_summary(pr, "created_at"))
        if len(pending) > max_per_section:
            lines.append(f"- _…et {len(pending) - max_per_section} de plus_")
    else:
        lines.append("- _aucune_")

    return "\n".join(lines)


async def _search_prs(github: ToolCallClient, query: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        result = await github.call_tool(
            tool_name="search_pull_requests",
            arguments={
                "query": query,
                "sort": "created",
                "order": "desc",
                "page": page,
                "perPage": PER_PAGE,
                "fields": PR_FIELDS,
            },
        )
        page_items = _extract_prs(_extract_tool_payload(result))
        items.extend(page_items)
        if len(page_items) < PER_PAGE:
            break
    return items


@workflows.activity(name="fetch-merged-prs")
async def fetch_merged_prs(
    repo: str,
    since: str,
    github: ToolCallClient = Depends(github_connector),
) -> list[dict[str, Any]]:
    return await _search_prs(github, f"repo:{repo} is:pr is:merged merged:>={since}")


@workflows.activity(name="fetch-pending-review-prs")
async def fetch_pending_review_prs(
    repo: str,
    github: ToolCallClient = Depends(github_connector),
) -> list[dict[str, Any]]:
    return await _search_prs(github, f"repo:{repo} is:pr is:open review:required")


@workflows.activity(name="resolve-slack-self")
async def resolve_slack_self(
    slack_user_id: str | None,
    slack: ToolCallClient = Depends(slack_connector),
) -> str:
    if slack_user_id:
        return slack_user_id
    result = await slack.call_tool(
        tool_name="slack_read_user_profile",
        arguments={},
    )
    payload = _extract_tool_payload(result)
    if isinstance(payload, dict):
        user = payload.get("user")
        if isinstance(user, dict) and user.get("id"):
            return str(user["id"])
        if payload.get("id"):
            return str(payload["id"])
    raise RuntimeError(
        "Could not resolve the Slack user id for the current user; "
        "pass slack_user_id in the workflow input"
    )


@workflows.activity(name="post-slack-digest")
async def post_slack_digest(
    channel_id: str,
    message: str,
    slack: ToolCallClient = Depends(slack_connector),
) -> None:
    await slack.call_tool(
        tool_name="slack_send_message",
        arguments={"channel_id": channel_id, "message": message},
    )


@workflows.workflow.define(
    name="repo-catchup",
    workflow_display_name="GitHub PR catchup",
    workflow_description=(
        "Given a GitHub repository and a date, lists pull requests merged since "
        "that date and pull requests still awaiting review, each with a one-line "
        "summary and a link, and posts the digest to the user's Slack self-chat."
    ),
    on_behalf_of=True,
)
@uses_connectors(github_connector, slack_connector)
class RepoCatchupWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: RepoCatchupInput) -> RepoCatchupResult:
        repo = validate_repo(input.repo)
        since = validate_since(input.since)

        merged = await fetch_merged_prs(repo, since)
        pending = await fetch_pending_review_prs(repo)
        digest = build_digest(repo, since, merged, pending, input.max_per_section)

        channel_id = await resolve_slack_self(input.slack_user_id)
        await post_slack_digest(channel_id, digest)

        return RepoCatchupResult(
            repo=repo,
            since=since,
            merged_count=len(merged),
            pending_review_count=len(pending),
            slack_channel_id=channel_id,
            digest=digest,
        )
