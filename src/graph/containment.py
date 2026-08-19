"""Child -> parent as a traversable `in` edge.

`permission_parent_entity_id` is the ACL chain. These edges are the same tree
made queryable: neighbors, `outgoing`, and the canvas relation list. They
confer no access.
"""

from __future__ import annotations

from core.graph import IN, Edge
from core.message import GraphWrite
from graph.protocol import GraphView


def in_edge(entity_id: str, parent_entity_id: str | None) -> list[Edge]:
    if not parent_entity_id:
        return []
    return [
        Edge(
            from_entity_id=entity_id,
            to_entity_id=parent_entity_id,
            relation=IN,
        )
    ]


async def with_parent(graph: GraphView, write: GraphWrite) -> GraphWrite:
    """Attach `in` to the permission parent; retract outgoing the write replaced."""
    parent = write.node.permission_parent_entity_id if write.node else None
    edges = [*write.edges, *in_edge(write.entity_id, parent)]
    wanted = {(e.to_entity_id, e.relation) for e in edges}
    retract = [
        old
        for old in await graph.edges_from(write.entity_id)
        if (old.to_entity_id, old.relation) not in wanted
    ]
    return write.model_copy(update={"edges": edges, "retract_edges": retract})
