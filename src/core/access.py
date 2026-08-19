"""Authorization vocabulary.

Grants are stored; effective access is derived by walking. Nothing here
materializes person -> resource, so revoking a container grant stays one row
delete rather than a fan-out over its subtree.

There is no canonical READ/WRITE/ADMIN vocabulary. A grant carries the source's
own role. Names below are what writers emit; the catalog and priorities live
in `access_level` and are comparable only within one source, which is all that
is ever needed, because a permission chain never crosses sources.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Identity(BaseModel):
    """A principal. Non-enumerable ones (a workspace, a domain, "anyone")
    become synthetic identities so a group change is one membership row.

    `email` is the only source-derived correspondence field on this model: the
    one key Slack, Notion, and Drive share. It is a hint for which app user
    matches which mirrored identity, never the link itself — that is an
    OAuth-proven membership. Identity ids are source-prefixed and minted by
    graph generators (`slack:user:U123`, `drive:user:{email}`), never by
    pollers. Drive people are `drive:user:{email}`, not a bare email.
    `public` (`core.identity.PUBLIC_ID`) is the one shared principal.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(
        description="Namespaced, e.g. 'slack:user:U123' or 'slack:workspace:T1'"
    )
    display_name: str | None = None
    # Correspondence hint only. Matching this to an app user's profile email
    # does not grant anything.
    email: str | None = None
    # Mirrored identities cannot log in. An app user reaches these grants via an
    # OAuth-proven membership edge — never via email match, which is a profile
    # field a user can set.
    can_authenticate: bool = False
    is_active: bool = True


class Membership(BaseModel):
    """child is a member of parent; grants flow from parent down to child."""

    model_config = ConfigDict(frozen=True)

    child_identity_id: str
    parent_identity_id: str


class AccessGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity_id: str
    # The node this grant is on, named as an entity id: generators run before
    # anything touches Postgres and never see a uuid.
    resource_entity_id: str
    # The source's own role, namespaced: 'drive:commenter', 'slack:member'.
    # Must be a row in access_level. Not collapsed onto a shared scale, because
    # 'commenter' and 'reader' both read and only one can annotate — a
    # distinction that is unrecoverable once discarded, and that nothing in
    # this system needs discarded.
    level: str


SLACK_MEMBER = "slack:member"
NOTION_VISIBLE = "notion:integration_visible"
NOTION_PUBLIC = "notion:public"


def drive_role(role: str) -> str:
    return f"drive:{role}"
