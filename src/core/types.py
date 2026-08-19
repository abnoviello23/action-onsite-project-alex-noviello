"""Closed node-type vocabulary.

One enum for envelopes, generators, and `node.node_type`. Values are
'{source}:{thing}'. Identity-only members never produce a queryable node.
"""

from __future__ import annotations

from enum import StrEnum


class NodeType(StrEnum):
    SLACK_WORKSPACE = "slack:workspace"
    SLACK_CHANNEL = "slack:channel"
    SLACK_MESSAGE = "slack:message"

    DRIVE_DRIVE = "drive:drive"
    DRIVE_FOLDER = "drive:folder"
    DRIVE_FILE = "drive:file"

    NOTION_WORKSPACE = "notion:workspace"
    NOTION_DATABASE = "notion:database"
    NOTION_DATA_SOURCE = "notion:data_source"
    NOTION_PAGE = "notion:page"


# A workspace is a principal, not a document. The worker still accepts the
# envelope — it carries identities and memberships — but nothing can query it.
IDENTITY_ONLY: frozenset[NodeType] = frozenset(
    {NodeType.SLACK_WORKSPACE, NodeType.NOTION_WORKSPACE}
)
