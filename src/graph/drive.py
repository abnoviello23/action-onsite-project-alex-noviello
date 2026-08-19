"""Drive envelopes -> graph writes.

Shared-drive, folder, and file nodes, plus the grants that hang on each.
ACL inherits along `permission_parent_entity_id`; containment is also an `in`
edge so folder ↔ file is traversable like Slack's `in_channel`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from connectors.drive.envelopes import DriveDriveFacts, DriveItemFacts
from connectors.drive.models import DriveFile, DriveUser, Permission, SharedDrive
from core.access import AccessGrant, Identity, drive_role
from core.graph import Node
from core.identity import PUBLIC_ID
from core.message import ChangeKind, Envelope, GraphWrite
from core.payloads import DriveDrivePayload, DriveFilePayload, DriveFolderPayload
from core.types import NodeType
from graph.containment import with_parent
from graph.links import urls_in, with_url_mentions
from graph.protocol import GraphView

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def drive_user(email: str) -> str:
    return f"drive:user:{email.lower()}"


def drive_group(key: str) -> str:
    return f"drive:group:{key.lower()}"


def drive_domain(domain: str) -> str:
    return f"drive:domain:{domain.lower()}"


def drive_unresolved(permission_id: str) -> str:
    return f"drive:unresolved:{permission_id}"


def identity_from_permission(permission: Permission) -> Identity:
    if permission.type == "anyone":
        return Identity(id=PUBLIC_ID, display_name="anyone with the link")
    if permission.type == "domain" and permission.domain:
        return Identity(id=drive_domain(permission.domain), display_name=permission.domain)
    if permission.type == "group" and permission.emailAddress:
        return Identity(
            id=drive_group(permission.emailAddress),
            display_name=permission.emailAddress,
            email=permission.emailAddress,
        )
    if permission.type == "user" and permission.emailAddress:
        return Identity(
            id=drive_user(permission.emailAddress),
            display_name=permission.emailAddress,
            email=permission.emailAddress,
            is_active=not permission.deleted,
        )
    return Identity(
        id=drive_unresolved(permission.id),
        display_name=f"{permission.type}/{permission.role}",
    )


def identity_from_drive_user(user: DriveUser | None) -> Identity | None:
    if user is None or not user.emailAddress:
        return None
    return Identity(
        id=drive_user(user.emailAddress),
        display_name=user.displayName or user.emailAddress,
        email=user.emailAddress,
    )


async def _deleted(env: Envelope, graph: GraphView) -> GraphWrite:
    return GraphWrite(
        node_type=env.node_type,
        entity_id=env.entity_id,
        change=ChangeKind.DELETED,
        retract_edges=await graph.edges_from(env.entity_id),
    )


def _access(
    permissions: list[Permission], entity_id: str
) -> tuple[list[AccessGrant], list[Identity]]:
    direct = [p for p in permissions if not p.is_inherited]
    identities = [identity_from_permission(p) for p in direct]
    grants = [
        AccessGrant(
            identity_id=identity.id,
            resource_entity_id=entity_id,
            level=drive_role(p.role),
        )
        # strict: the two lists are built in lockstep from the same
        # permissions, so a length mismatch is a bug rather than a short zip.
        for p, identity in zip(direct, identities, strict=True)
    ]
    return grants, identities


def _drive_write(drive: SharedDrive, permissions: list[Permission]) -> GraphWrite:
    created = drive.createdTime or EPOCH
    entity_id = drive.entity_id
    grants, identities = _access(permissions, entity_id)
    return GraphWrite(
        node_type=NodeType.DRIVE_DRIVE,
        entity_id=entity_id,
        node=Node(
            node_type=NodeType.DRIVE_DRIVE,
            entity_id=entity_id,
            permission_parent_entity_id=None,
            body=drive.name,
            created_at=created,
            updated_at=created,
            content_version=created.isoformat(),
            payload=DriveDrivePayload(drive_id=drive.id, name=drive.name).model_dump(
                mode="json"
            ),
        ),
        grants=grants,
        identities=identities,
    )


def _item_write(
    file: DriveFile,
    *,
    permissions: list[Permission],
    body: str,
    body_source: str,
    change: ChangeKind,
    link_urls: list[str] | None = None,
) -> GraphWrite:
    entity_id = file.entity_id
    created = file.createdTime or EPOCH
    actor = identity_from_drive_user(file.lastModifyingUser)
    parent = file.parent_entity_id

    if file.is_folder:
        node_type = NodeType.DRIVE_FOLDER
        node_body = file.name
        payload = DriveFolderPayload(
            file_id=file.id,
            name=file.name,
            drive_id=file.driveId,
            parent_id=file.parent_id,
            version=file.version,
            trashed=file.trashed,
            web_view_link=file.webViewLink,
        )
    else:
        node_type = NodeType.DRIVE_FILE
        node_body = f"{file.name}\n\n{body}".strip() if body else file.name
        payload = DriveFilePayload(
            file_id=file.id,
            name=file.name,
            mime_type=file.mimeType,
            drive_id=file.driveId,
            parent_id=file.parent_id,
            actor_id=actor.id if actor else None,
            version=file.version,
            head_revision_id=file.headRevisionId,
            md5_checksum=file.md5Checksum,
            size=file.size,
            trashed=file.trashed,
            body_source=body_source,
            last_modifying_email=(
                file.lastModifyingUser.emailAddress if file.lastModifyingUser else None
            ),
            web_view_link=file.webViewLink,
            link_urls=link_urls or [],
        )

    grants, identities = _access(permissions, entity_id)
    if actor:
        identities = [*identities, actor]

    return GraphWrite(
        node_type=node_type,
        entity_id=entity_id,
        change=change,
        node=Node(
            node_type=node_type,
            entity_id=entity_id,
            permission_parent_entity_id=parent,
            body=node_body,
            created_at=created,
            updated_at=file.modifiedTime or created,
            content_version=file.content_version,
            payload=payload.model_dump(mode="json"),
        ),
        grants=grants,
        identities=identities,
    )


def _drive_needles(file: DriveFile) -> list[str]:
    return [n for n in (file.id, file.webViewLink) if n]


class DriveDriveGenerator:
    node_type = NodeType.DRIVE_DRIVE

    async def generate(self, env: Envelope, graph: GraphView) -> GraphWrite:
        if env.change is ChangeKind.DELETED:
            return await _deleted(env, graph)
        facts = DriveDriveFacts.model_validate(env.payload)
        return _drive_write(facts.drive, facts.permissions)


class DriveFolderGenerator:
    node_type = NodeType.DRIVE_FOLDER

    async def generate(self, env: Envelope, graph: GraphView) -> GraphWrite:
        if env.change is ChangeKind.DELETED:
            return await _deleted(env, graph)
        facts = DriveItemFacts.model_validate(env.payload)
        return await with_parent(
            graph,
            await with_url_mentions(
                graph,
                _item_write(
                    facts.file,
                    permissions=facts.permissions,
                    body=facts.body,
                    body_source=facts.body_source,
                    change=env.change,
                ),
                [],
                needles=_drive_needles(facts.file),
            ),
        )


class DriveFileGenerator:
    node_type = NodeType.DRIVE_FILE

    async def generate(self, env: Envelope, graph: GraphView) -> GraphWrite:
        if env.change is ChangeKind.DELETED:
            return await _deleted(env, graph)
        facts = DriveItemFacts.model_validate(env.payload)
        link_urls = urls_in(facts.body)
        return await with_parent(
            graph,
            await with_url_mentions(
                graph,
                _item_write(
                    facts.file,
                    permissions=facts.permissions,
                    body=facts.body,
                    body_source=facts.body_source,
                    change=env.change,
                    link_urls=link_urls,
                ),
                link_urls,
                needles=_drive_needles(facts.file),
            ),
        )
