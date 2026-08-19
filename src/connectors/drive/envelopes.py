"""Self-contained Drive facts for the worker. No graph shape."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from connectors.drive.models import DriveFile, Permission, SharedDrive, drive_entity_id
from core.message import ChangeKind, Envelope
from core.types import NodeType


class DriveDriveFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    drive: SharedDrive
    permissions: list[Permission] = Field(default_factory=list)


class DriveItemFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    file: DriveFile
    permissions: list[Permission] = Field(default_factory=list)
    body: str = ""
    body_source: str = "none"


def _item_type(file: DriveFile) -> NodeType:
    return NodeType.DRIVE_FOLDER if file.is_folder else NodeType.DRIVE_FILE


def drive_envelope(drive: SharedDrive, *, permissions: list[Permission]) -> Envelope:
    return Envelope(
        node_type=NodeType.DRIVE_DRIVE,
        entity_id=drive.entity_id,
        partition_key=drive.entity_id,
        payload=DriveDriveFacts(drive=drive, permissions=permissions).model_dump(
            mode="json"
        ),
    )


def item_envelope(
    file: DriveFile,
    *,
    permissions: list[Permission],
    body: str,
    body_source: str,
    change: ChangeKind = ChangeKind.CREATED,
) -> Envelope:
    node_type = _item_type(file)
    return Envelope(
        node_type=node_type,
        entity_id=file.entity_id,
        partition_key=file.entity_id,
        change=change,
        payload=DriveItemFacts(
            file=file,
            permissions=permissions,
            body=body,
            body_source=body_source,
        ).model_dump(mode="json"),
    )


def delete_envelope(file_id: str, *, was_folder: bool = False) -> Envelope:
    node_type = NodeType.DRIVE_FOLDER if was_folder else NodeType.DRIVE_FILE
    entity_id = drive_entity_id(file_id)
    return Envelope(
        node_type=node_type,
        entity_id=entity_id,
        partition_key=entity_id,
        change=ChangeKind.DELETED,
    )
