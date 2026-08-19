"""Loading the ontology into the process, and the ontology every stack starts with.

`load` is the seam that keeps `core` free of database imports. It reads the
active `semantic_config`, compiles each declared type into a `NodeTypeSpec`, and
pushes the result into `core.registry.SEMANTIC_TYPES` — after which an entity
type is indistinguishable from a source type to every reader.

Who calls it, and why each one has to:

  * **migrate** — before compiling views, or `person` has no view to query.
  * **api** — before serving, or the agent's schema digest omits the semantic
    half and `query_type('person')` is rejected as an unknown type.
  * **semantic worker** — before consuming, for both of the above reasons.

Processes that never read semantic types (the poller, the ingest worker) do not
call it and hold an empty registry, which is correct rather than merely
tolerated: neither can be made to write an entity by accident.

The default ontology below is seeded once, when the table is empty. It is a
starting point, not a fixture — `semantic.config.publish` replaces it, and
nothing here overwrites a config that already exists.
"""

from __future__ import annotations

import asyncio
import logging
import time

import asyncpg

from core.registry import register_semantic
from core.types import NodeType
from semantic.config import (
    SemanticConfig,
    SemanticEntityType,
    SemanticField,
    load_active,
    publish,
)

log = logging.getLogger("semantic.registry")

_DOCUMENT_SOURCES = [
    str(NodeType.SLACK_MESSAGE),
    str(NodeType.NOTION_PAGE),
    str(NodeType.DRIVE_FILE),
]

_IDENTITY_RULE = (
    "\n\nIdentity is the part that matters, and it is all this node stores. "
    "Anything you learn *about* it from this document is a fact, recorded "
    "separately — do not try to put it here."
)


# The starting vocabulary. Three types, because they are the three that every
# other relation in a workspace hangs off: who is involved, what body of work it
# belongs to, and what has to happen.
DEFAULT_CONFIG = SemanticConfig(
    types=[
        SemanticEntityType(
            name="person",
            description=(
                "A human being who acts in this workspace or is referred to "
                "across it — an employee, a contractor, a named counterparty at "
                "another company. Not a bot, not a team, not a company."
            ),
            extract_prompt=(
                "Surface people this document acts on or refers to: the author, "
                "anyone @-mentioned, anyone named in the prose, and anyone an "
                "assignment or decision is attributed to.\n\n"
                "Identifiers are stronger than names, but only when they belong "
                "to the person you are naming. `user_id` identifies whoever "
                "posted the document and `mentioned_user_ids` the accounts it "
                "@-mentions; attach one to a person only when you are confident "
                "it is that person.\n\n"
                "In particular, a body that opens `Some Name: ...` is quoting or "
                "attributing to that name, which is often NOT the account that "
                "posted it. Never attach `user_id` to a name you read out of the "
                "text on the assumption they are the same person — give that "
                "person their name and no id. Attaching one person's id to "
                "another merges two real people into a single record, which is "
                "the worst error available here and no later document can undo "
                "it.\n\n"
                "Use an email when the text gives one. A name on its own is a "
                "perfectly good identity: use the fullest form you can see, and "
                "do not split one person into two entries because the document "
                "referred to them two ways." + _IDENTITY_RULE
            ),
            identity=[
                SemanticField(
                    name="name", description="Fullest human-readable name available."
                ),
                SemanticField(
                    name="email",
                    description="Work email, only if the document states it.",
                ),
                SemanticField(
                    name="slack_user_id",
                    description=(
                        "Slack user id like U123ABC, from the document's "
                        "user_id or mentioned_user_ids. Never guessed."
                    ),
                ),
            ],
            identity_keys=["slack_user_id", "email", "name"],
            source_types=_DOCUMENT_SOURCES,
        ),
        SemanticEntityType(
            name="project",
            description=(
                "A named, ongoing body of work that documents and tasks belong "
                "to — a migration, a launch, a named system under active "
                "development. Internal delivery work, not a sales opportunity."
            ),
            extract_prompt=(
                "Surface named initiatives, products, or workstreams this "
                "document treats as an ongoing thing rather than a one-off.\n\n"
                "Use the name the organisation uses, in its fullest form. Only "
                "surface a project the document actually names; do not invent "
                "one to group things that merely appeared together.\n\n"
                "Work being sold to a customer is a `deal`, not a project, even "
                "when the document calls it a project. The test is who it is "
                "for: a named account with a stage and a close date is a deal; "
                "something the company is building for itself is a project."
                + _IDENTITY_RULE
            ),
            identity=[
                SemanticField(name="name", description="The project's own name."),
            ],
            identity_keys=["name"],
            source_types=_DOCUMENT_SOURCES,
        ),
        SemanticEntityType(
            name="deal",
            description=(
                "A sales opportunity with a named customer or prospect — the "
                "account being sold to, tracked through stages toward a close. "
                "Distinct from a `project`, which is work the company does for "
                "itself. The account is the thing that persists: one account "
                "has one deal entity, whatever a document happens to call it."
            ),
            extract_prompt=(
                "Surface customer accounts this document treats as a sales "
                "opportunity: prospects, pipeline, renewals, expansions, "
                "anything with a stage, a close date, an ARR figure, or an "
                "owning rep.\n\n"
                "`account` is the customer's own name and nothing else — "
                "'Cobalt Financial', not 'Cobalt Financial renewal' or 'the "
                "Cobalt deal'. Strip the words deal, renewal, expansion, "
                "opportunity, and any quarter or year. Two documents about the "
                "same customer must produce the same account, or the pipeline "
                "splits in two.\n\n"
                "A customer merely mentioned as a reference or an existing "
                "user is not a deal. There has to be something being sold."
                + _IDENTITY_RULE
            ),
            identity=[
                SemanticField(
                    name="account",
                    description=(
                        "The customer organisation's name, on its own. No deal, "
                        "renewal, quarter, or descriptive suffix."
                    ),
                ),
                SemanticField(
                    name="stage",
                    description=(
                        "Pipeline stage, only if the document states it — "
                        "discovery, negotiation, closed won, and so on."
                    ),
                ),
                SemanticField(
                    name="owner",
                    description="Name of the rep who owns it, if stated.",
                ),
            ],
            # The account alone. Stage and owner change over a deal's life and
            # would split one opportunity into several entities as it moved
            # through the pipeline — which is the failure this key prevents.
            identity_keys=["account"],
            source_types=_DOCUMENT_SOURCES,
        ),
        SemanticEntityType(
            name="task",
            description=(
                "A concrete piece of work someone is expected to do, specific "
                "enough that you could tell whether it had been done."
            ),
            extract_prompt=(
                "Surface work someone is expected to do: commitments ('I'll "
                "write the migration'), assignments ('can you take the on-call "
                "doc'), and open action items in a list.\n\n"
                "Write `title` as a short imperative phrase — 'write the "
                "migration', not 'Alex said he would write the migration'. Keep "
                "it specific enough that two different tasks never produce the "
                "same title, because the title is the identity.\n\n"
                "Do not create a task for finished work, for a topic of "
                "discussion, or for a vague aspiration with no action. Most "
                "documents contain no tasks, and returning none is correct."
                + _IDENTITY_RULE
            ),
            identity=[
                SemanticField(
                    name="title",
                    description="Short imperative phrase naming the work.",
                ),
            ],
            identity_keys=["title"],
            source_types=_DOCUMENT_SOURCES,
        ),
    ]
)


async def load(conn: asyncpg.Connection) -> SemanticConfig:
    """Read the active config and install it. Seeds the default if none exists.

    Returns what was installed so a caller that needs the prompts — the
    extractor — does not have to read the table a second time.
    """
    config = await load_active(conn)
    if config is None:
        version = await publish(conn, DEFAULT_CONFIG)
        config = DEFAULT_CONFIG.model_copy(update={"version": version})
        log.info("seeded default semantic ontology as version %d", version)

    register_semantic(config.node_specs())
    log.info(
        "semantic config v%d: %s",
        config.version,
        ", ".join(sorted(config.type_names)) or "(no types)",
    )
    return config


class ActiveConfig:
    """A process-wide handle on the ontology, safe to share across tasks.

    Two problems this exists to solve, both invisible in a single-threaded read.

    `register_semantic` clears the global registry before repopulating it, so a
    concurrent `SemanticNode` validation can observe it empty and reject a type
    that is perfectly valid. The lock closes that window.

    And re-reading `semantic_config` on every job is a query per extraction to
    answer a question whose answer changes maybe weekly. The TTL makes a
    published revision take effect on its own without paying for the check each
    time.

    Description and prompt revisions land within one TTL. Adding or removing an
    identity *field* still needs a restart, because a view is DDL and only
    migrate builds it — a fresh spec here without its column would compile
    predicates against a view that does not have them.
    """

    def __init__(self, *, ttl_seconds: float = 60.0) -> None:
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._config: SemanticConfig | None = None
        self._checked_at = 0.0

    async def get(self, conn: asyncpg.Connection) -> SemanticConfig:
        now = time.monotonic()
        if self._config is not None and now - self._checked_at < self._ttl:
            return self._config

        async with self._lock:
            # Re-checked inside the lock: several tasks can arrive together on
            # expiry and only the first should do the read.
            now = time.monotonic()
            if self._config is not None and now - self._checked_at < self._ttl:
                return self._config

            config = await load_active(conn)
            if config is None:
                config = await load(conn)
            elif self._config is None or config.version != self._config.version:
                register_semantic(config.node_specs())
                log.info("semantic config now v%d", config.version)

            self._config = config
            self._checked_at = time.monotonic()
            return config
