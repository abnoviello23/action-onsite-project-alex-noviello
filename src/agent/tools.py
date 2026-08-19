"""Tool schemas for the orchestrator and the walkers, and their dispatch.

Two disjoint tool sets, and the split is the cost control. The orchestrator can
issue global lookups (`query_type`, `semantic_search`) and fan out; walkers can
only move along edges from a node they were handed. A walker that needs a new
global lookup says so in its note and the orchestrator issues it on the next
turn — which keeps fan-out decisions, and their budget, on one loop.

`strict: true` is deliberately not set. Strict tool use requires a fully
constrained schema, and `Predicate.value` is genuinely polymorphic — text, int,
bool, timestamp, or a list, depending on the field it applies to. Rather than
flatten that into an `anyOf` the model has to reason about, validation lives in
`query.compile`, whose errors are written for the model to read and retry
against ("`name` is not a column of slack:message; available: …"). A rejected
query costs one turn and teaches; a mis-specified schema costs every turn.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.actions import ACTIONS
from core.registry import SEMANTIC_TYPES, all_specs
from query.compile import MAX_QUERY_LIMIT, Op, QueryError, TypeQuery, describe_types
from query.entities import (
    DEFAULT_ENTITY_LIMIT,
    MAX_CONSTRAINTS,
    MAX_ENTITY_LIMIT,
    EntitySearchError,
)
from query.search import DEFAULT_K, MAX_K, SearchError
from query.session import (
    DEFAULT_NEIGHBORS,
    MAX_NEIGHBORS,
    NodeBudgetExceeded,
    SessionGraph,
)
from semantic.config import FACT_RELATION, FACT_TYPE

log = logging.getLogger("agent.tools")

_OP_NAMES = sorted(str(o) for o in Op)


def _node_type_names() -> list[str]:
    """Every queryable type, read at call time rather than at import.

    Semantic types are registered from the database during boot, which happens
    after this module is imported. A constant captured here would list the
    source half only, and the enum in a tool schema is what stops the model from
    asking for a type the compiler will reject.
    """
    return sorted(all_specs())

_DIRECTION = {
    "type": "string",
    "enum": ["out", "in", "both"],
    "description": (
        "Which way to traverse. 'out' follows edges this node points along "
        "(a message's `in` leads to its channel); 'in' follows edges that point "
        "at it (a channel's `in` leads to its messages, `mentions` gives the "
        "backlinks, `next` gives the previous message). Default 'both'."
    ),
}

def query_type_tool() -> dict[str, Any]:
    return {
        "name": "query_type",
        "description": (
            "Filter one node type by its columns. This is the precise tool: use it "
            "to resolve a container by name (a channel called 'eng', a folder, a "
            "Notion database), then bind the id it returns and query the items "
            "inside it. Prefer indexed columns — an unfiltered scan of a large type "
            "will be capped and report itself truncated. Returns summaries, not "
            "full bodies; open a result with `explore` to read it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_type": {"type": "string", "enum": _node_type_names()},
                "predicates": {
                    "type": "array",
                    "description": "ANDed together. Omit to list the type.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {
                                "type": "string",
                                "description": "A column of this node type.",
                            },
                            "op": {"type": "string", "enum": _OP_NAMES},
                            "value": {
                                "description": (
                                    "Matched to the column's type. `ilike` takes a "
                                    "literal substring (not a pattern); `fts` takes "
                                    "words and applies only to `body`; `has` tests "
                                    "membership of a list column; `is_null` takes "
                                    "true or false; timestamps are ISO-8601."
                                )
                            },
                        },
                        "required": ["field", "op"],
                    },
                },
                "limit": {
                    "type": "integer",
                    "description": f"Visible results to return, at most {MAX_QUERY_LIMIT}.",
                },
            },
            "required": ["node_type"],
        },
    }

def semantic_search_tool() -> dict[str, Any]:
    return {
        "name": "semantic_search",
        "description": (
            "Find documents by meaning rather than by exact words. This is the "
            "recall tool: use it when you do not know which container to look in, "
            "or when the wording in the question is unlikely to appear verbatim. "
            "Only substantive bodies are indexed — channel names, folder names, and "
            "very short messages are not, so use `query_type` to find those."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to search for."},
                "k": {
                    "type": "integer",
                    "description": f"Results to return (default {DEFAULT_K}, max {MAX_K}).",
                },
                "node_types": {
                    "type": "array",
                    "description": "Optional filter.",
                    "items": {"type": "string", "enum": _node_type_names()},
                },
            },
            "required": ["text"],
        },
    }

EXPLORE = {
    "name": "explore",
    "description": (
        "Read the given nodes and walk their local neighbourhood in parallel, "
        "one worker per node, each answering the question you give it. Use this "
        "once you have promising candidates: it is how you get from a summary "
        "to actual content and the surrounding thread, document, or backlinks. "
        "Workers cannot search; if one reports that something needs a global "
        "lookup, issue that query yourself on your next turn."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity_ids": {
                "type": "array",
                "description": "Seeds, from a previous result. Best few only.",
                "items": {"type": "string"},
            },
            "question": {
                "type": "string",
                "description": (
                    "What each worker should find out. Self-contained — workers "
                    "cannot see the conversation or each other."
                ),
            },
        },
        "required": ["entity_ids", "question"],
    },
}

_FINISH_DESCRIPTION = (
    "Answer the user and end the run. Cite the entity ids you actually "
    "read. If nothing visible answered the question, say so plainly — do "
    "not speculate about material you could not see, and do not imply that "
    "anything exists beyond what you found."
)

_PLAN_SCHEMA = {
    "type": "array",
    "description": (
        "Actions to carry out the request, in the order they must run. Include "
        "this ONLY when the user asked for something to be done. A question "
        "gets an answer and no plan.\n\n"
        "Nothing here runs when you emit it. The plan goes back to the person "
        "who asked, who reads it and decides — so write it to be read: name a "
        "real target, say why it, and put the exact text you would send in "
        "`params`."
    ),
    "items": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": (
                    "Short label for this step, like 'a1'. Later steps refer "
                    "to it. Letters, digits, - and _ only."
                ),
            },
            "action": {"type": "string"},
            "entity_id": {
                "type": "string",
                "description": (
                    "The node to act on — an id from your own results, never a "
                    "name and never a guess. It must be of the node type the "
                    "action applies to: post to a channel's id, reply to a "
                    "message's id, create a file in a folder's id."
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "Arguments for this action, matching its params. A string "
                    "may contain {{step_id.field}} to use an earlier step's "
                    "result — see the Acting section."
                ),
            },
            "rationale": {
                "type": "string",
                "description": (
                    "Why this target and not another, in one line. The person "
                    "reviewing cannot see how you chose it."
                ),
            },
        },
        "required": ["id", "action", "entity_id", "params", "rationale"],
    },
}

FINISH = {
    "name": "finish",
    "description": _FINISH_DESCRIPTION,
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "citations": {
                "type": "array",
                "description": "Entity ids supporting the answer.",
                "items": {"type": "string"},
            },
        },
        "required": ["answer"],
    },
}


def finish_tool(can_act: bool) -> dict[str, Any]:
    """`finish`, with the plan field only when actions are switched on.

    Built per request rather than kept as a second constant. With
    `ACTIONS_ENABLED` false there is nothing that could run a plan, and offering
    the field anyway would invite the model to spend a turn writing one that
    gets dropped on the floor — worse than useless, because the answer would
    describe writes that were never going to happen.
    """
    if not can_act:
        return FINISH
    schema = {
        **FINISH["input_schema"],
        "properties": {**FINISH["input_schema"]["properties"], "plan": _PLAN_SCHEMA},
    }
    return {"name": "finish", "description": _FINISH_DESCRIPTION, "input_schema": schema}

GET = {
    "name": "get",
    "description": "Read one node in full: body, payload, and source ids.",
    "input_schema": {
        "type": "object",
        "properties": {"entity_id": {"type": "string"}},
        "required": ["entity_id"],
    },
}

NEIGHBORS = {
    "name": "neighbors",
    "description": (
        "Edges incident to this node, each labelled with the direction it was "
        "traversed in. Read the relation and direction together — they mean "
        "different things each way round. Pass `query` to get the neighbours "
        "that bear on a question rather than the most recent ones."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "node_types": {
                "type": "array",
                "description": (
                    "Only peers of these types. Use it to pull one slice whole "
                    "— every `fact` about a person, every `person` on a project "
                    "— rather than a mixed page you have to sift."
                ),
                "items": {"type": "string"},
            },
            "direction": _DIRECTION,
            "query": {
                "type": "string",
                "description": (
                    "Rank the neighbours by relevance to this, most relevant "
                    "first, and drop the ones it does not bear on. Without it "
                    "you get the most recently updated — which is rarely the "
                    "order you want when a node has more neighbours than your "
                    "limit. Use it whenever you are looking for something "
                    "specific rather than surveying."
                ),
            },
            "limit": {
                "type": "integer",
                "description": f"Default {DEFAULT_NEIGHBORS}, max {MAX_NEIGHBORS}.",
            },
        },
        "required": ["entity_id"],
    },
}

FOLLOW = {
    "name": "follow",
    "description": (
        "`neighbors` narrowed to one relation. `follow(msg, 'in', 'out')` is the "
        "channel that contains a message; `follow(chan, 'in', 'in')` is the "
        "messages it contains."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "relation": {"type": "string"},
            "node_types": {
                "type": "array",
                "description": (
                    "Only peers of these types. Use it to pull one slice whole "
                    "— every `fact` about a person, every `person` on a project "
                    "— rather than a mixed page you have to sift."
                ),
                "items": {"type": "string"},
            },
            "direction": _DIRECTION,
            "query": {
                "type": "string",
                "description": (
                    "Rank the neighbours by relevance to this, most relevant "
                    "first, and drop the ones it does not bear on. Without it "
                    "you get the most recently updated — which is rarely the "
                    "order you want when a node has more neighbours than your "
                    "limit. Use it whenever you are looking for something "
                    "specific rather than surveying."
                ),
            },
            "limit": {
                "type": "integer",
                "description": f"Default {DEFAULT_NEIGHBORS}, max {MAX_NEIGHBORS}.",
            },
        },
        "required": ["entity_id", "relation"],
    },
}

def find_entities_tool() -> dict[str, Any]:
    """Compound entity search. Built per call — the entity vocabulary is loaded
    from the database at boot, after this module is imported."""
    entities = sorted(t for t in SEMANTIC_TYPES if t != FACT_TYPE)
    return {
        "name": "find_entities",
        "description": (
            "Find people, projects, or other entities that satisfy SEVERAL "
            "conditions at once. Give one condition per `constraints` entry, "
            "each phrased as its own short statement — do not put the whole "
            "question in one string.\n\n"
            "This is the tool for questions of the form 'who does X and also "
            "Y'. Each condition is searched separately over what is known "
            "about each entity and the results are intersected, because the "
            "two halves are usually recorded in different places: someone's "
            "project work and their departure are not written down together. "
            "One combined search matches neither half well.\n\n"
            "Results are ranked by how many conditions each entity met and say "
            "which, with the statement that satisfied each. An entity meeting "
            "some but not all is still returned — read `matched` against "
            "`total` before concluding anything."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_type": {
                    "type": "string",
                    "enum": entities,
                    "description": "The kind of entity to return.",
                },
                "constraints": {
                    "type": "array",
                    "description": (
                        "One condition per entry, each independently checkable. "
                        "For 'people on Atlas who left recently': "
                        "['works on the Atlas project', 'has left or been "
                        "offboarded recently']."
                    ),
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_CONSTRAINTS,
                },
                "limit": {
                    "type": "integer",
                    "description": f"Entities to return, at most {MAX_ENTITY_LIMIT}.",
                },
            },
            "required": ["node_type", "constraints"],
        },
    }


def orchestrator_tools(can_act: bool = False) -> list[dict[str, Any]]:
    """Built per request, not at import.

    Two of these schemas enumerate node types, and the semantic half of that
    vocabulary is registered from the database during boot — after this module
    is imported. A constant captured at import time would advertise the source
    types only, and the model would never think to ask for a `person`.
    """
    tools = [query_type_tool(), semantic_search_tool()]
    # Only offered once an entity vocabulary exists. With no semantic types
    # registered it would advertise an empty enum, which reads to the model
    # as a usable tool that rejects every call.
    if any(t != FACT_TYPE for t in SEMANTIC_TYPES):
        tools.append(find_entities_tool())
    return [*tools, EXPLORE, finish_tool(can_act)]


# No node-type enums in these, so they are fixed.
WALKER_TOOLS = [GET, NEIGHBORS, FOLLOW]


def registry_digest() -> str:
    """The queryable schema, for the orchestrator's system prompt.

    Emitted from the same specs the views are compiled from, so a payload change
    updates the model's picture on the next boot instead of drifting against a
    hand-maintained copy. Semantic types are marked, because the difference
    matters to how they should be read: a source node is a document somebody
    wrote, an inferred one is a conclusion this system drew from documents.
    """
    lines: list[str] = []
    for spec in describe_types():
        cols = spec["columns"]
        indexed = sorted(n for n, c in cols.items() if c["indexed"])
        plain = sorted(n for n, c in cols.items() if not c["indexed"])
        mark = "  [inferred]" if spec["node_type"] in SEMANTIC_TYPES else ""
        lines.append(f"{spec['node_type']}{mark}")
        if indexed:
            lines.append(f"  indexed: {', '.join(indexed)}")
        lines.append(f"  other:   {', '.join(plain)}")
    return "\n".join(lines)


def action_digest() -> str:
    """The write catalog, for the orchestrator's system prompt.

    Emitted from `core.actions` for the same reason `registry_digest` is emitted
    from the type specs: a hand-maintained copy of this in the prompt would
    drift, and the failure would be silent — the model would propose an action
    that no longer exists, or miss one that does, and either way the plan would
    only fail at execution.

    The node type is on every line because it is the single most common way a
    plan is wrong: posting to a message id rather than a channel id looks right
    and is not.
    """
    lines: list[str] = []
    for spec in ACTIONS.values():
        mark = "  [replaces existing content]" if spec.destructive else ""
        lines.append(f"{spec.name}  ->  acts on a {spec.node_type} node{mark}")
        lines.append(f"    {spec.summary}")
        schema = spec.json_schema()
        required = set(schema.get("required") or ())
        lines.append("    params:")
        for name, field in (schema.get("properties") or {}).items():
            lines.append(f"      {_param_digest(name, field, name in required)}")
        if spec.returns:
            lines.append(f"    returns: {', '.join(spec.returns)}")
    return "\n".join(lines)


def _param_digest(name: str, field: dict[str, Any], required: bool) -> str:
    """One parameter, with what it will actually accept.

    Names alone are not enough, and the failure mode is specific: a parameter
    constrained to a few literals reads as free text, so the model supplies a
    reasonable-sounding value that is not one of them — `if_exists='create_new'`
    rather than `'version'` — and the plan dies at validation having looked
    perfectly sensible to everyone who read it.

    Taken from the same JSON Schema the validator is built from, so an enum
    gaining a member updates this on the next boot instead of drifting against a
    hand-written list.
    """
    if choices := field.get("enum"):
        kind = "one of: " + " | ".join(str(c) for c in choices)
    else:
        kind = str(field.get("type", "value"))

    detail = [kind, "required"] if required else [kind]
    if not required and "default" in field:
        detail.append(f"default {json.dumps(field['default'])}")
    return f"{name} — {', '.join(detail)}"


def semantic_relation_digest() -> str:
    """How the inferred half of the graph is shaped, for the prompts.

    Deliberately a description rather than a list. Relations between entities
    are drawn by the extractor as it finds them, not declared in config, so any
    enumeration here would be a snapshot that goes stale — and the agent can see
    the real ones on any node with `neighbors`.
    """
    if not SEMANTIC_TYPES:
        return ""
    entities = sorted(t for t in SEMANTIC_TYPES if t != FACT_TYPE)
    if not entities:
        return ""
    return (
        "Inferred entities and facts:\n"
        "\n"
        f"  {', '.join(entities)} are entities. Each holds identity only — a "
        f"name, an email, an id — and nothing else. They are the index into the "
        f"graph: resolve one by name, then look at what hangs off it.\n"
        "\n"
        f"  `{FACT_TYPE}` nodes are what is known about an entity, one claim per "
        f"node, each read out of a specific document. Reach them with "
        f"`follow(<entity>, '{FACT_RELATION}', 'in')`. Every fact carries the "
        f"access of the document it came from, so the set you get back is the "
        f"set this user is allowed to know — two people asking the same entity "
        f"the same question can correctly get different answers, and neither "
        f"can tell the other's exist.\n"
        "\n"
        "  Entities are also linked to each other by relations the extractor "
        "chose (`works_on`, `owns`, `blocks`, …). These are not a fixed "
        "vocabulary; read them off `neighbors` rather than assuming a name.\n"
        "\n"
        "A fact is this system's reading of a document, not the document. When "
        "the answer is a fact about the world, follow the fact to its `source` "
        "and cite that."
    )


async def run_walker_tool(
    graph: SessionGraph, name: str, args: dict[str, Any]
) -> tuple[str, bool]:
    """Dispatch a walker tool. Returns `(result_text, is_error)`."""
    try:
        if name == "get":
            node = await graph.get(args["entity_id"])
            if node is None:
                return _absent(args["entity_id"]), False
            return node.model_dump_json(), False

        if name == "neighbors":
            page = await graph.neighbors(
                args["entity_id"],
                node_types=args.get("node_types"),
                direction=args.get("direction", "both"),
                query=args.get("query"),
                limit=int(args.get("limit", DEFAULT_NEIGHBORS)),
            )
            return _page(page), False

        if name == "follow":
            page = await graph.follow(
                args["entity_id"],
                args["relation"],
                node_types=args.get("node_types"),
                direction=args.get("direction", "both"),
                query=args.get("query"),
                limit=int(args.get("limit", DEFAULT_NEIGHBORS)),
            )
            return _page(page), False

    except NodeBudgetExceeded as exc:
        return str(exc), True
    except KeyError as exc:
        return f"missing required argument: {exc}", True
    except (ValueError, TypeError) as exc:
        return str(exc), True

    return f"unknown tool {name!r}", True


async def run_orchestrator_query(
    graph: SessionGraph, name: str, args: dict[str, Any]
) -> tuple[str, bool]:
    """Dispatch `query_type` / `semantic_search`. `explore` is handled by the
    orchestrator itself, which owns the fan-out budget."""
    try:
        if name == "query_type":
            result = await graph.query_type(TypeQuery.model_validate(args))
            return result.model_dump_json(), False

        if name == "find_entities":
            result = await graph.find_entities(
                args["node_type"],
                list(args.get("constraints") or []),
                limit=int(args.get("limit", DEFAULT_ENTITY_LIMIT)),
            )
            return result.model_dump_json(), False

        if name == "semantic_search":
            result = await graph.semantic_search(
                args["text"],
                k=int(args.get("k", DEFAULT_K)),
                node_types=args.get("node_types"),
            )
            return result.model_dump_json(), False

    except (QueryError, SearchError, EntitySearchError) as exc:
        # Written for the model: it names the offending field and lists what is
        # available, so the next turn is a corrected query rather than a repeat.
        return str(exc), True
    except KeyError as exc:
        return f"missing required argument: {exc}", True
    except (ValueError, TypeError) as exc:
        return str(exc), True

    return f"unknown tool {name!r}", True


def _absent(entity_id: str) -> str:
    """Invisible and nonexistent give the same answer, deliberately: a distinct
    'exists but forbidden' would confirm the node to anyone who asked."""
    return json.dumps({"found": False, "entity_id": entity_id})


def _page(page) -> str:
    """Neighbours plus whether they are all of them.

    `complete` is not decoration. A walker that cannot tell a full list from the
    first 25 of 40 will summarise from part of the evidence and cite it with
    full confidence, and "there is nothing about that" stops being
    distinguishable from "I stopped looking".
    """
    return json.dumps(
        {
            "neighbors": [n.model_dump(mode="json") for n in page.neighbors],
            "complete": page.complete,
        }
    )
