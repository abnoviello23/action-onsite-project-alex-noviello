"""node_type -> generator. Adding a type is a visible edit here."""

from __future__ import annotations

from core.types import NodeType
from graph.drive import DriveDriveGenerator, DriveFileGenerator, DriveFolderGenerator
from graph.notion import (
    NotionDatabaseGenerator,
    NotionDataSourceGenerator,
    NotionPageGenerator,
    NotionWorkspaceGenerator,
)
from graph.protocol import Generator
from graph.slack import (
    SlackChannelGenerator,
    SlackMessageGenerator,
    SlackWorkspaceGenerator,
)

_GENERATORS: tuple[Generator, ...] = (
    SlackWorkspaceGenerator(),
    SlackChannelGenerator(),
    SlackMessageGenerator(),
    DriveDriveGenerator(),
    DriveFolderGenerator(),
    DriveFileGenerator(),
    NotionWorkspaceGenerator(),
    NotionDatabaseGenerator(),
    NotionDataSourceGenerator(),
    NotionPageGenerator(),
)

GENERATORS: dict[NodeType, Generator] = {g.node_type: g for g in _GENERATORS}

missing = [t for t in NodeType if t not in GENERATORS]
if missing:
    raise RuntimeError(f"no generator for {missing}")


def generator_for(node_type: NodeType) -> Generator:
    try:
        return GENERATORS[node_type]
    except KeyError as exc:
        raise KeyError(f"no generator for {node_type}") from exc
