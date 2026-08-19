"""What is worth sending to the embedder.

Semantic search is for documents and structured data — Drive files, Notion
pages and database rows, long Slack messages. Container labels (`#eng`, a
folder name, a database title) are found by typed-view filters, and embedding
them just pollutes ANN with every query that shares a word of the name.

The chunker still applies `worth_embedding` as a backstop so a caller that
forgets the type allowlist cannot turn a channel name into a vector.
"""

from __future__ import annotations

from core.types import NodeType

# Bodies that can actually be about something. Identity-only types never reach
# here; channels/folders/databases are queryable but they are names, not docs.
EMBEDDABLE_NODE_TYPES: frozenset[NodeType] = frozenset(
    {
        NodeType.SLACK_MESSAGE,
        NodeType.DRIVE_FILE,
        NodeType.NOTION_PAGE,
    }
)

# A single short line is a label. Channel names cap at 80; file names with no
# extracted body are the same shape. Newlines mean structure (Notion properties,
# markdown, CSV) and those may be shorter than this and still be data.
_LABEL_LINE_CHARS = 120
_ABSOLUTE_MIN_CHARS = 40

# The built-in fact type, spelled rather than imported. `embed` is reachable from
# the ingest worker, and the semantic package depends on `store` which the ingest
# worker owns — one string is a cheaper coupling than a cycle. Same trade, and
# same constant, as `store.FACT_TYPE`.
FACT_TYPE = "fact"

# Shortest fact worth a vector. Low on purpose: this is screening out an empty
# extraction, not judging whether a claim is substantial. "Jane was offboarded"
# is nineteen characters and is the entire answer to a question someone will ask.
_FACT_MIN_CHARS = 12


def worth_embedding(content: str) -> bool:
    """Whether `content` is a document/row rather than a name."""
    text = content.strip()
    if len(text) < _ABSOLUTE_MIN_CHARS:
        return False
    if "\n" not in text and len(text) < _LABEL_LINE_CHARS:
        return False
    return True


def should_embed(node_type: NodeType | str | None, content: str) -> bool:
    """Type allowlist and the label backstop together.

    `None` means the caller did not say what it was sending, so the text is
    judged on its own. That is the chunker's path: it applies this same function
    as a backstop, and a request that names its `kind` gets the type-aware
    answer while one that does not falls back to the shape heuristic.

    An unrecognised type is not embeddable rather than an error. Semantic types
    are the case that matters: `person` is not a `NodeType` member, and its body
    is a name — exactly what the allowlist above exists to exclude. Raising here
    would turn "this node is not a document" into a crash in the embed writer.

    Facts are the deliberate exception, and they are checked before the enum
    lookup because they are not a `NodeType` member either. They need their own
    rule: `worth_embedding` distinguishes a document from a *label*, and a fact
    is neither. It is a claim, written as prose by the extractor because it had
    something to say — "Jane is leading the Atlas rollback" is 34 characters on
    one line and would fail both heuristics, while being exactly the sentence
    entity search has to match. Facts are documents by construction, so the only
    thing left to screen out is an empty one.
    """
    if node_type is None:
        return worth_embedding(content)
    if node_type == FACT_TYPE:
        return len(content.strip()) >= _FACT_MIN_CHARS
    try:
        kind = node_type if isinstance(node_type, NodeType) else NodeType(node_type)
    except ValueError:
        return False
    if kind not in EMBEDDABLE_NODE_TYPES:
        return False
    return worth_embedding(content)
