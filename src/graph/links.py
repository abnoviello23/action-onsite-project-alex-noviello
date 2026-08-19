"""Cross-source `mentions` edges from URLs in a document body.

Connectors leave outbound URLs on the payload (`link_urls`). Generators turn
the ones that already resolve to a live node into edges. No new nodes: if the
Drive file a Slack message named has not been ingested yet, there is no edge
until one side or the other is generated against a graph that has both.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import parse_qs, urlparse

from core.graph import MENTIONS, Edge
from core.message import GraphWrite
from graph.protocol import GraphView

# Mirrored entity ids are `{source}:{native}`. Semantic ids have no colon in
# the source slot (`person:name:…` still has colons, but not a connector
# prefix). Used to retract stale URL-mentions without touching document ->
# entity edges the extractor wrote under the same relation name.
_MIRRORED = ("slack:", "drive:", "notion:")

_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)

_DRIVE_PATH = re.compile(
    r"(?:docs\.google\.com/(?:document|spreadsheets|presentation|forms|file)/d/"
    r"(?!e/)"
    r"|drive\.google\.com/file/d/"
    r"|drive\.google\.com/drive/(?:u/\d+/)?folders/)"
    r"(?P<id>[\w-]+)",
    re.I,
)
_SLACK_PERMALINK = re.compile(
    r"slack\.com/archives/(?P<channel>[CGD][A-Z0-9]+)/p(?P<ts>\d{10,})",
    re.I,
)
_NOTION_HOST = re.compile(
    r"(?:^|\.)(?:notion\.so|notion\.site|notion\.com)$", re.I
)
_NOTION_ID = re.compile(
    r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)


def urls_in(text: str) -> list[str]:
    """Bare http(s) URLs in prose, trailing punctuation stripped."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _URL.finditer(text or ""):
        url = match.group(0).rstrip(".,;:)")
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def entity_ids_from_urls(urls: Sequence[str]) -> list[str]:
    """Candidate mirrored entity ids a URL might name, de-duplicated."""
    found: list[str] = []
    seen: set[str] = set()
    for url in urls:
        for entity_id in _parse(url):
            if entity_id not in seen:
                seen.add(entity_id)
                found.append(entity_id)
    return found


def _parse(url: str) -> list[str]:
    host = (urlparse(url).hostname or "").lower()
    out: list[str] = []

    if "google.com" in host:
        path_match = _DRIVE_PATH.search(url)
        if path_match:
            out.append(f"drive:{path_match.group('id')}")
        query_id = parse_qs(urlparse(url).query).get("id", [None])[0]
        if query_id and "drive.google.com" in host:
            out.append(f"drive:{query_id}")

    if _NOTION_HOST.search(host):
        ids = _NOTION_ID.findall(urlparse(url).path)
        if ids:
            out.append(f"notion:{ids[-1].replace('-', '').lower()}")

    slack = _SLACK_PERMALINK.search(url)
    if slack:
        digits = slack.group("ts")
        ts = f"{digits[:-6]}.{digits[-6:]}" if len(digits) >= 16 else digits
        out.append(f"slack:{slack.group('channel')}:{ts}")

    return out


def _mirrored(entity_id: str) -> bool:
    return entity_id.startswith(_MIRRORED)


async def with_url_mentions(
    graph: GraphView,
    write: GraphWrite,
    urls: Sequence[str],
    *,
    needles: Sequence[str] = (),
    retract_stale: bool = False,
) -> GraphWrite:
    """Attach `mentions` to live nodes named by `urls`, and the reverse.

    Forward: parse each URL to a candidate entity id and keep it only when
    that row already exists. Reverse: documents ingested earlier stored the
    URL on `link_urls`; `needles` (a file id, a page id, a permalink
    fragment) find those documents so the edge is minted when the *target*
    arrives.

    `retract_stale` drops mirrored `mentions` this node used to have that
    the new URL list no longer justifies. Drive/Notion leave this off —
    `with_parent` already retracts outgoing the write replaced. Slack has
    no parent walk, so it opts in.
    """
    if write.node is None:
        return write

    from_id = write.entity_id
    extra: list[Edge] = []
    live = await graph.existing(entity_ids_from_urls(urls))
    for target in sorted(live):
        if target != from_id:
            extra.append(
                Edge(
                    from_entity_id=from_id,
                    to_entity_id=target,
                    relation=MENTIONS,
                )
            )

    for linker in await graph.mentioning(list(needles)):
        if linker != from_id:
            extra.append(
                Edge(
                    from_entity_id=linker,
                    to_entity_id=from_id,
                    relation=MENTIONS,
                )
            )

    seen = {(e.from_entity_id, e.to_entity_id, e.relation) for e in write.edges}
    edges = list(write.edges)
    for edge in extra:
        key = (edge.from_entity_id, edge.to_entity_id, edge.relation)
        if key not in seen:
            seen.add(key)
            edges.append(edge)

    retract = list(write.retract_edges)
    if retract_stale:
        wanted = {
            (e.to_entity_id, e.relation)
            for e in edges
            if e.from_entity_id == from_id
        }
        for old in await graph.edges_from(from_id):
            if (
                old.relation == MENTIONS
                and _mirrored(old.to_entity_id)
                and (old.to_entity_id, old.relation) not in wanted
            ):
                retract.append(old)

    return write.model_copy(update={"edges": edges, "retract_edges": retract})
