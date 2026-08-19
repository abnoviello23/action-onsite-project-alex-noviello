"""What can be *done* to a node, as opposed to what can be read from it.

Defined separately from the graph and scoped by node type, because the verb is a
property of the kind of thing: a Slack channel can be posted to, a Drive file can
be rewritten, and neither statement makes sense about the other. Nothing in
`core.graph` knows this module exists — an action is a capability the graph
happens to have a handle for, not a field on a node.

The catalog is code and the `action` table is a projection of it, reflected on
every boot the same way node-type views are. That direction matters: an action
is a function somebody has to have written, so a row appearing in the table
without an executor behind it would be a promise nothing can keep.

Five things every spec ties together:

  * the **node type** it applies to, which is what makes `actions_for` a lookup
    rather than a filter;
  * the **native keys** it needs, which is why `core.labels.native_keys` puts
    source ids on every summary — an action is dispatched with the source's own
    identifiers, never with our `entity_id`;
  * the **params** model, which is both the validator and the JSON Schema handed
    to an API caller or a model;
  * the **level** it requires, so that seeing a document and being allowed to
    overwrite it stay two different questions;
  * what it **returns**, so a later step in a plan can say "post the link to the
    thing you just created" without anyone inventing a template language.

`PlannedAction` lives here rather than in `actions`, and that is deliberate. The
planner is the retrieval agent, which must be able to describe a write without
being able to perform one; giving it the shape from `core` keeps it from
importing the module that holds the executors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.labels import native_keys
from core.types import NodeType

MAX_TEXT_CHARS = 40_000
MAX_NAME_CHARS = 255

# The declared entity type `slack.dm` addresses. Spelled rather than imported
# for the same reason `query.visibility` spells 'fact': the semantic vocabulary
# is loaded from the database at boot, and importing it here would invert the
# dependency between `core` and the layer built on top of it. If an operator
# renames the type in `semantic_config`, `actions_for` stops offering the DM and
# nothing else breaks — which is the right failure for a catalog entry whose
# subject no longer exists.
PERSON_TYPE = "person"

# The longest plan that will be accepted. Enough for "write a document, file it,
# announce it, and tell two people", and small enough that a runaway plan is a
# rejection rather than an afternoon of writes — a plan needing more than this is
# really several intents.
#
# Here rather than in `actions.plan` because the planner is told the number in
# its prompt, and the planner is the retrieval agent, which must not import the
# module holding the executors.
MAX_PLAN_STEPS = 8

# What to do when a create action finds something already at that name.
#
# No 'replace' member. Overwriting on a name collision would make a create
# quietly destructive, and the catalog already has an explicit verb for
# overwriting a document you have named.
IfExists = Literal["fail", "version"]


class PostMessageParams(BaseModel):
    """A new top-level message in a channel."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class ReplyParams(BaseModel):
    """A reply on the thread a message belongs to.

    No `thread_ts` argument. Which thread a reply lands in follows from the
    message being replied to, and taking it as a parameter would let a caller
    reply to one message inside a different message's thread.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class DirectMessageParams(BaseModel):
    """A direct message to the person this node names.

    No recipient argument, for the same reason `ReplyParams` has no thread: who
    it reaches follows from the node being acted on. Taking a user id as a
    parameter would let a caller address one person through another's record,
    and the audit row would then name the wrong subject.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class ReplaceContentParams(BaseModel):
    """Overwrite a Drive file's body."""

    model_config = ConfigDict(frozen=True)

    content: str = Field(max_length=MAX_TEXT_CHARS)


class CreateFileParams(BaseModel):
    """A new document in a Drive folder."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    content: str = Field(max_length=MAX_TEXT_CHARS)
    # Converted by Drive into a native Doc on upload. Off gives a plain text
    # file, which round-trips exactly but is not what anyone means by "prepare a
    # document and put it in the folder".
    as_document: bool = True
    if_exists: IfExists = "fail"


class AppendBlocksParams(BaseModel):
    """Append markdown to the end of a Notion page."""

    model_config = ConfigDict(frozen=True)

    markdown: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class CreatePageParams(BaseModel):
    """A new child page under a Notion page."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    markdown: str = Field(default="", max_length=MAX_TEXT_CHARS)
    if_exists: IfExists = "fail"


@dataclass(frozen=True)
class ActionSpec:
    """One thing that can be done to one kind of node."""

    name: str
    # A `NodeType` member, or a declared entity type name. Typed as `str`
    # because `NodeType` is a `StrEnum` and both forms compare equal to the
    # `node.node_type` column they are matched against.
    node_type: str
    summary: str
    params: type[BaseModel]
    # Payload fields the executor needs to address the thing in its own source.
    # Checked before dispatch, so a node missing one fails with a message about
    # the missing id rather than with a KeyError inside a connector.
    requires_native: tuple[str, ...]
    # Fields the executor promises to put in its result, and therefore the only
    # ones a later step of a plan may bind to. The **first is the canonical
    # reference** — the one recorded in `action_invocation.result_ref`, and the
    # handle that makes the write findable from outside this system.
    returns: tuple[str, ...] = ()
    # The `access_level` a principal must hold on the target, or None when the
    # source has no such notion. Compared by priority, and only ever against
    # levels from the same source: a node belongs to exactly one source, so
    # every grant on its ancestor chain speaks that source's vocabulary.
    requires_level: str | None = None
    # True when the action replaces existing content rather than adding to it.
    # Surfaced to callers because "append a paragraph" and "overwrite the file"
    # deserve different amounts of hesitation.
    destructive: bool = False

    @property
    def result_ref_field(self) -> str | None:
        """Which returned field is the one worth recording as the reference."""
        return self.returns[0] if self.returns else None

    def json_schema(self) -> dict[str, Any]:
        schema = self.params.model_json_schema()
        # Pydantic emits a `title` from the class name, which leaks
        # `ReplaceContentParams` into an API surface that has no use for it.
        schema.pop("title", None)
        return schema


_SPECS: tuple[ActionSpec, ...] = (
    ActionSpec(
        name="slack.post_message",
        node_type=NodeType.SLACK_CHANNEL,
        summary="Post a new message to this Slack channel.",
        params=PostMessageParams,
        requires_native=("channel_id",),
        returns=("ts", "channel_id", "permalink"),
        # Slack has no read-only channel membership: being in a channel is the
        # right to post in it. The check is real and simply never disagrees with
        # visibility, which is a property of Slack rather than a gap here.
        requires_level="slack:member",
    ),
    ActionSpec(
        name="slack.reply_in_thread",
        node_type=NodeType.SLACK_MESSAGE,
        summary="Reply in this message's thread.",
        params=ReplyParams,
        requires_native=("channel_id", "ts"),
        returns=("ts", "channel_id", "permalink"),
        requires_level="slack:member",
    ),
    ActionSpec(
        name="slack.dm",
        node_type=PERSON_TYPE,
        summary=(
            "Send a Slack direct message to this person. Available only on a "
            "person whose record carries a Slack user id."
        ),
        params=DirectMessageParams,
        requires_native=("slack_user_id",),
        returns=("ts", "channel_id", "permalink"),
        # No level, and none is missing. A `person` is inferred, so it carries
        # no grants of its own — and a Slack DM has no ACL either: any member of
        # a workspace may message any other. Visibility of the person is
        # therefore the whole gate, and it is not a weak one, because being able
        # to see someone here confers nothing that opening Slack would not.
        requires_level=None,
    ),
    ActionSpec(
        name="drive.replace_content",
        node_type=NodeType.DRIVE_FILE,
        summary="Replace the entire body of this Drive document.",
        params=ReplaceContentParams,
        requires_native=("file_id",),
        returns=("file_id", "web_view_link"),
        requires_level="drive:writer",
        destructive=True,
    ),
    ActionSpec(
        name="drive.create_file",
        node_type=NodeType.DRIVE_FOLDER,
        summary="Create a new document inside this Drive folder.",
        params=CreateFileParams,
        requires_native=("file_id",),
        returns=("file_id", "web_view_link"),
        # Writer is the bar Drive itself applies to adding a child, and it is
        # the one level in this catalog that regularly refuses somebody: a
        # `drive:commenter` can read every word of a folder and may not add to
        # it.
        requires_level="drive:writer",
    ),
    ActionSpec(
        name="notion.append_blocks",
        node_type=NodeType.NOTION_PAGE,
        summary="Append markdown blocks to the end of this Notion page.",
        params=AppendBlocksParams,
        requires_native=("page_id",),
        returns=("page_id", "url"),
        # The weakest bar in the catalog, and honestly so. An internal Notion
        # integration cannot read per-user page capabilities, so the connector
        # mirrors only 'this is visible to the integration' — which means anyone
        # who can see a page here will be allowed to append to it. Narrowing
        # this needs a finer grant from the connector, not a stricter constant.
        requires_level="notion:integration_visible",
    ),
    ActionSpec(
        name="notion.create_page",
        node_type=NodeType.NOTION_PAGE,
        summary="Create a new child page under this Notion page.",
        params=CreatePageParams,
        requires_native=("page_id",),
        returns=("page_id", "url"),
        requires_level="notion:integration_visible",
    ),
)

ACTIONS: dict[str, ActionSpec] = {spec.name: spec for spec in _SPECS}

_BY_TYPE: dict[str, tuple[ActionSpec, ...]] = {}
for _spec in _SPECS:
    _BY_TYPE[_spec.node_type] = (*_BY_TYPE.get(_spec.node_type, ()), _spec)


def actions_for(node_type: str | None) -> tuple[ActionSpec, ...]:
    """Every action defined for this node type.

    Empty for most inferred types, and that remains the rule: a `project` is a
    conclusion this system drew, not a thing in a source with an id you can
    write to, and acting on the documents behind it means following its edges to
    them.

    `person` is the one exception, and it is an exception to the reading of that
    rule rather than to the rule itself. It carries a Slack user id — issued by
    Slack, not inferred by us — so it is an address that routes to a real
    conversation. The entity is still not the thing written to; the DM is.
    """
    if not node_type:
        return ()
    return _BY_TYPE.get(node_type, ())


def address_of(
    spec: ActionSpec, node_type: str | None, payload: dict[str, Any]
) -> dict[str, str]:
    """The source ids this action will be dispatched with.

    `native_keys` answers "what are this node's source identifiers", which it
    knows only for mirrored types — an entity is not in its table at all. This
    asks the narrower question a dispatcher actually has: what does *this*
    action need in order to address the thing. Anything the projection did not
    supply is read off the payload by name, which is how `slack_user_id` reaches
    the DM executor without `core.labels` having to learn about the ontology.

    Filling gaps rather than branching on "is this semantic" keeps one path for
    both cases. What is still missing afterwards is the caller's to report, so
    that the complaint can name the action that wanted it.
    """
    address = dict(native_keys(node_type, payload))
    for key in spec.requires_native:
        if address.get(key):
            continue
        value = payload.get(key)
        if isinstance(value, str) and value:
            address[key] = value
    return address


class PlannedAction(BaseModel):
    """One action a caller intends to run, before anything has run.

    The unit the retrieval agent emits and the plan runner consumes. It is data,
    not a promise: nothing in it has been checked, and every field is re-resolved
    and re-validated at dispatch. That is what makes it safe to hand a plan to a
    client and take it back again — the trust boundary is the runner, so a plan
    read out of a request body is worth exactly what one read out of our own
    table would be, and neither has to be stored to be trustworthy.
    """

    model_config = ConfigDict(frozen=True)

    # Referenced by later steps in `{{id.field}}` bindings, so it is constrained
    # to what can appear unambiguously inside one.
    id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    action: str = Field(min_length=1, max_length=64)
    entity_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    # Why this target, in the planner's own words. Nothing that executes reads
    # it; it exists because a plan is reviewed by a person, and "post to this
    # channel" is not reviewable without "because the rollout thread is here".
    rationale: str = ""
    # Filled in by the planner from the graph, for that same review. Never read
    # back as authority — the runner resolves `entity_id` itself.
    target_label: str = ""
    target_type: str | None = None


__all__ = [
    "ACTIONS",
    "MAX_NAME_CHARS",
    "MAX_PLAN_STEPS",
    "MAX_TEXT_CHARS",
    "PERSON_TYPE",
    "ActionSpec",
    "IfExists",
    "PlannedAction",
    "actions_for",
    "address_of",
]
