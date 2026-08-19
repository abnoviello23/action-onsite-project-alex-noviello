"""Generator protocol: envelope + graph view -> GraphWrite."""

from __future__ import annotations

from typing import Protocol

from core.graph import Edge
from core.message import Envelope, GraphWrite
from core.types import NodeType


class GraphView(Protocol):
    """Source-neutral reads. Never the source APIs."""

    async def node_payload(self, entity_id: str) -> dict | None: ...

    async def outgoing(self, entity_id: str, relation: str) -> list[str]: ...

    async def edges_from(self, entity_id: str) -> list[Edge]: ...

    async def content_version(self, entity_id: str) -> str | None: ...

    async def existing(self, entity_ids: list[str]) -> set[str]: ...

    async def mentioning(self, needles: list[str]) -> list[str]: ...


class SlackGraphView(GraphView, Protocol):
    """Slack `next` splice: predecessor and successor in a channel or thread."""

    async def slack_neighbors(
        self,
        *,
        channel_id: str,
        ts: str,
        thread_ts: str | None,
        exclude: str,
    ) -> tuple[str | None, str | None]: ...


class Generator(Protocol):
    node_type: NodeType

    async def generate(self, env: Envelope, graph: GraphView) -> GraphWrite | None: ...
