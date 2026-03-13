"""Normalize Jira API response dicts to domain WorkItem types.

The ``atlassian-python-api`` library returns plain Python dicts from the
Jira REST API. This module maps those structures to our frozen domain
dataclasses.
"""

from __future__ import annotations

from datetime import UTC, datetime

from appif.domain.work_tracking.models import (
    IssueTypeInfo,
    ItemAttachment,
    ItemAuthor,
    ItemComment,
    ItemLink,
    LinkType,
    LinkTypeInfo,
    ProjectInfo,
    TransitionInfo,
    WorkItem,
)

# ---------------------------------------------------------------------------
# Link type mapping
# ---------------------------------------------------------------------------

_LINK_MAP: dict[tuple[str, str], LinkType] = {
    ("blocks", "outward"): LinkType.BLOCKS,
    ("blocks", "inward"): LinkType.BLOCKED_BY,
    ("is blocked by", "outward"): LinkType.BLOCKED_BY,
    ("is blocked by", "inward"): LinkType.BLOCKS,
    ("duplicate", "outward"): LinkType.DUPLICATES,
    ("duplicate", "inward"): LinkType.DUPLICATED_BY,
    ("cloners", "outward"): LinkType.DUPLICATES,
    ("cloners", "inward"): LinkType.DUPLICATED_BY,
    ("relates", "outward"): LinkType.RELATES_TO,
    ("relates", "inward"): LinkType.RELATES_TO,
}

_FALLBACK_LINK_TYPE = LinkType.RELATES_TO


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_datetime(value: str | None) -> datetime:
    """Parse Jira's ISO 8601 datetime string to a timezone-aware datetime."""
    if not value:
        return datetime(1970, 1, 1, tzinfo=UTC)
    # Jira Cloud format: "2026-01-15T10:30:00.000+0000"
    cleaned = value.replace("+0000", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            return datetime(1970, 1, 1, tzinfo=UTC)


def _to_author(raw: dict | None) -> ItemAuthor | None:
    """Convert a Jira user dict to ItemAuthor, or None if absent."""
    if not raw:
        return None
    account_id = raw.get("accountId") or raw.get("key") or ""
    display = raw.get("displayName") or ""
    return ItemAuthor(id=str(account_id), display_name=display)


def _get_description(fields: dict) -> str:
    """Extract description, handling both string and ADF formats."""
    desc = fields.get("description")
    if desc is None:
        return ""
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        return _extract_adf_text(desc)
    return str(desc)


def _extract_adf_text(node: dict) -> str:
    """Recursively extract plain text from an ADF document."""
    if node.get("type") == "text":
        return node.get("text", "")
    content = node.get("content", [])
    parts = [_extract_adf_text(child) for child in content if isinstance(child, dict)]
    if node.get("type") in ("paragraph", "heading", "bulletList", "orderedList"):
        return "\n".join(parts)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_issue(issue: dict) -> WorkItem:
    """Convert a Jira issue dict to a domain ``WorkItem``."""
    fields = issue.get("fields", {})

    # Links
    links: list[ItemLink] = []
    for raw_link in fields.get("issuelinks") or []:
        link_type_obj = raw_link.get("type", {})
        link_type_name = link_type_obj.get("name", "").lower()

        if "outwardIssue" in raw_link and raw_link["outwardIssue"]:
            direction = "outward"
            target_key = raw_link["outwardIssue"]["key"]
        elif "inwardIssue" in raw_link and raw_link["inwardIssue"]:
            direction = "inward"
            target_key = raw_link["inwardIssue"]["key"]
        else:
            continue

        domain_type = _LINK_MAP.get((link_type_name, direction), _FALLBACK_LINK_TYPE)
        links.append(ItemLink(link_type=domain_type, target_key=target_key))

    # Comments
    comments: list[ItemComment] = []
    comment_container = fields.get("comment", {})
    raw_comments = comment_container.get("comments") if isinstance(comment_container, dict) else []
    for c in raw_comments or []:
        author = _to_author(c.get("author"))
        if author is None:
            author = ItemAuthor(id="unknown", display_name="Unknown")
        comments.append(
            ItemComment(
                id=str(c.get("id", "")),
                author=author,
                body=c.get("body", "") or "",
                created=_parse_datetime(c.get("created")),
            )
        )

    # Sub-tasks
    subtasks = fields.get("subtasks") or []
    sub_item_keys = tuple(st.get("key", "") for st in subtasks)

    # Parent
    parent = fields.get("parent")
    parent_key = parent.get("key") if parent else None

    # Priority
    priority_obj = fields.get("priority")
    priority = priority_obj.get("name") if priority_obj else None

    # Issue type
    issue_type_obj = fields.get("issuetype", {})
    item_type = (issue_type_obj.get("name") or "unknown").lower()

    # Status
    status_obj = fields.get("status", {})
    status = status_obj.get("name") or "unknown"

    # Attachments
    attachments = [normalize_attachment(a) for a in fields.get("attachment") or []]

    return WorkItem(
        key=issue.get("key", ""),
        id=str(issue.get("id", "")),
        title=fields.get("summary") or "",
        description=_get_description(fields),
        status=status,
        item_type=item_type,
        created=_parse_datetime(fields.get("created")),
        updated=_parse_datetime(fields.get("updated")),
        priority=priority,
        labels=tuple(fields.get("labels") or []),
        assignee=_to_author(fields.get("assignee")),
        reporter=_to_author(fields.get("reporter")),
        parent_key=parent_key,
        sub_item_keys=sub_item_keys,
        links=tuple(links),
        comments=tuple(comments),
        attachments=tuple(attachments),
    )


def normalize_comment(raw: dict) -> ItemComment:
    """Convert a Jira comment dict to a domain ``ItemComment``."""
    author = _to_author(raw.get("author"))
    if author is None:
        author = ItemAuthor(id="unknown", display_name="Unknown")
    return ItemComment(
        id=str(raw.get("id", "")),
        author=author,
        body=raw.get("body", "") or "",
        created=_parse_datetime(raw.get("created")),
    )


def normalize_transition(raw: dict) -> TransitionInfo:
    """Convert a Jira transition dict to domain ``TransitionInfo``."""
    return TransitionInfo(
        id=str(raw.get("id", "")),
        name=raw.get("name", ""),
    )


def normalize_issue_type(raw: dict) -> IssueTypeInfo:
    """Convert a Jira issue type dict to domain ``IssueTypeInfo``."""
    return IssueTypeInfo(
        name=raw.get("name", ""),
        subtask=bool(raw.get("subtask", False)),
        description=raw.get("description", ""),
    )


def normalize_link_type_info(raw: dict) -> LinkTypeInfo:
    """Convert a Jira issue link type dict to domain ``LinkTypeInfo``."""
    return LinkTypeInfo(
        name=raw.get("name", ""),
        inward=raw.get("inward", ""),
        outward=raw.get("outward", ""),
    )


def normalize_attachment(raw: dict) -> ItemAttachment:
    """Convert a Jira attachment dict to domain ``ItemAttachment``.

    Jira attachment fields:
    - id, filename, mimeType, size, created, author, content (URL)

    The platform-specific ``content`` URL is intentionally excluded.
    Callers use ``WorkTracker.download_attachment(id)`` instead.
    """
    return ItemAttachment(
        id=str(raw.get("id", "")),
        filename=raw.get("filename", ""),
        mime_type=raw.get("mimeType", "application/octet-stream"),
        size_bytes=int(raw.get("size", 0)),
        created=_parse_datetime(raw.get("created")),
        author=_to_author(raw.get("author")),
    )


def normalize_project(raw: dict) -> ProjectInfo:
    """Convert a Jira project dict to domain ``ProjectInfo``.

    Jira project fields used:
    - key, name, description, lead, projectTypeKey, self
    """
    lead_raw = raw.get("lead")
    lead = _to_author(lead_raw) if lead_raw else None

    return ProjectInfo(
        key=raw.get("key", ""),
        name=raw.get("name", ""),
        description=raw.get("description") or "",
        lead=lead,
        project_type=raw.get("projectTypeKey", ""),
        url=raw.get("self", ""),
    )
