"""Environment-backed configuration shared by every service."""

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


POSTGRES_USER = _env("POSTGRES_USER", "pkg")
POSTGRES_PASSWORD = _env("POSTGRES_PASSWORD", "pkg_local_dev")
POSTGRES_DB = _env("POSTGRES_DB", "pkg")
POSTGRES_HOST = _env("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(_env("POSTGRES_PORT", "5432"))

# Postgres connections per process. The API needs the most: an agent run fans
# out across walkers, tool calls, and constraints, each holding one.
DB_POOL_SIZE = int(_env("DB_POOL_SIZE", "20"))

REDIS_HOST = _env("REDIS_HOST", "redis")
REDIS_PORT = int(_env("REDIS_PORT", "6379"))

# Producer and consumer must agree on this. See docker-compose.yml.
NUM_PARTITIONS = int(_env("NUM_PARTITIONS", "2"))

# Worker-only; unset elsewhere.
PARTITION = _env("PARTITION")
CONSUMER_NAME = _env("CONSUMER_NAME")

LOG_LEVEL = _env("LOG_LEVEL", "INFO")

# BGE-small chunker. Internal compose DNS; the host port is EMBED_HOST_PORT.
EMBED_URL = _env("EMBED_URL", "http://embed:8080")
EMBED_MODEL = _env("EMBED_MODEL", "BAAI/bge-small-en-v1.5")

# --- Vector index ---
# pgvector 0.8+. Without iterative scan an HNSW scan takes a fixed `ef_search`
# window and hands back whatever survives the visibility filter, so a principal
# who can see 1% of the corpus gets an empty page from a query with plenty of
# matches. Iterative scan keeps pulling instead. Set empty to disable on an
# older pgvector, which falls back to the over-fetch loop in `query.search`.
HNSW_ITERATIVE_SCAN = _env("HNSW_ITERATIVE_SCAN", "relaxed_order")
HNSW_EF_SEARCH = int(_env("HNSW_EF_SEARCH", "100"))

# --- Agent ---
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
ANTHROPIC_BASE_URL = _env("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_VERSION = _env("ANTHROPIC_VERSION", "2023-06-01")
ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-sonnet-5")
# Thinking depth and overall token spend. Sent as output_config.effort; empty
# leaves the API default (high). Sampling parameters do not exist on the current
# models — temperature/top_p/top_k are rejected — so this is the only knob.
ANTHROPIC_EFFORT = _env("ANTHROPIC_EFFORT")

# Orchestrator budget. Parallel tool calls inside one Anthropic turn count once.
AGENT_MAX_TURNS = int(_env("AGENT_MAX_TURNS", "8"))
AGENT_MAX_WALKERS = int(_env("AGENT_MAX_WALKERS", "4"))
AGENT_WALKER_MAX_HOPS = int(_env("AGENT_WALKER_MAX_HOPS", "6"))
# Hard ceiling on nodes opened in full across one request, orchestrator and
# walkers together. Bounds context spend and the blast radius of a loop.
AGENT_MAX_NODES = int(_env("AGENT_MAX_NODES", "60"))

# --- Semantic layer ---
# Off by default. Extraction spends a model call per ingested document, so it is
# opt-in rather than something a plain `compose up` starts billing for.
SEMANTIC_ENABLED = _env("SEMANTIC_ENABLED", "false").lower() == "true"
SEMANTIC_MODEL = _env("SEMANTIC_MODEL", "claude-sonnet-5")
SEMANTIC_EFFORT = _env("SEMANTIC_EFFORT", "low")
# Concurrent extractions in one worker. Each is one model call plus a short
# transaction, so this is bounded by the Anthropic rate limit rather than by
# Postgres.
SEMANTIC_CONCURRENCY = int(_env("SEMANTIC_CONCURRENCY", "4"))
# Tool-loop turns per document. The extractor names entities, sees what the
# graph already knows about them, records facts, and draws links, so it needs
# more than one round — but a document that has not finished in this many is
# looping rather than working.
SEMANTIC_MAX_TURNS = int(_env("SEMANTIC_MAX_TURNS", "8"))
# 'anthropic' (default) or 'openai'. The second is a compatibility shim for
# evaluating extraction without an Anthropic key — see `agent.openai_client`.
# It is not a second supported backend; the retrieval agent always uses
# Anthropic.
SEMANTIC_PROVIDER = _env("SEMANTIC_PROVIDER", "anthropic").lower()

OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_BASE_URL = _env("OPENAI_BASE_URL", "https://api.openai.com")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4o-mini")
# Re-offering the whole corpus is a deliberate act (`python -m semantic
# --backfill`), not a background loop, so there is no sweep interval to tune.
# The ingest worker's enqueue is idempotent and self-heals a lost publish.

# --- Actions ---
# Write access to the sources, off by default. Reading a mirrored graph is safe;
# posting to a real Slack channel is not, and the difference should be a
# deliberate switch rather than a default.
ACTIONS_ENABLED = _env("ACTIONS_ENABLED", "false").lower() == "true"

# Identities the X-Demo-Identity header may assume, comma-separated. Empty
# disables the header entirely, which is what production looks like: there is no
# "become anyone" switch unless someone deliberately lists who anyone may be.
AGENT_DEMO_IDENTITIES = [
    i.strip() for i in _env("AGENT_DEMO_IDENTITIES").split(",") if i.strip()
]

# --- Sources ---
# Bot token drives every Web API call; the app token only opens the Socket Mode
# WebSocket and cannot call the Web API at all.
SLACK_BOT_TOKEN = _env("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = _env("SLACK_APP_TOKEN")
SLACK_POLL_INTERVAL_SECONDS = float(_env("SLACK_POLL_INTERVAL_SECONDS", "30"))
SLACK_INCLUDE_PRIVATE = _env("SLACK_INCLUDE_PRIVATE", "true").lower() == "true"

# Path to the service account JSON key, inside the container. Drive uses a
# service account rather than user OAuth because nothing here can complete a
# browser consent flow, and an unpublished app's refresh token expires weekly.
GOOGLE_APPLICATION_CREDENTIALS = _env("GOOGLE_APPLICATION_CREDENTIALS")
# Shared drive ids, comma-separated. Each becomes a root node the first time it
# is seen, and carries its own changes.list pageToken.
DRIVE_IDS = [d.strip() for d in _env("DRIVE_IDS").split(",") if d.strip()]
DRIVE_POLL_INTERVAL_SECONDS = float(_env("DRIVE_POLL_INTERVAL_SECONDS", "30"))

# Internal integration secret. Notion has no equivalent of Slack's app token:
# one credential covers every endpoint, and the integration's capabilities —
# set in the Notion UI, not here — decide what it may do.
NOTION_TOKEN = _env("NOTION_TOKEN")
# Pinned deliberately. 2025-09-03 split databases into data sources, 2026-02-01
# halved the default page size, 2026-03-11 renamed archived to in_trash. Bumping
# this is a migration.
NOTION_VERSION = _env("NOTION_VERSION", "2026-03-11")
# Slower than Slack and Drive on purpose: a sweep costs one search page plus one
# call per changed page, against a ~3 req/s budget, and Notion timestamps are
# minute-granular so a faster cycle cannot see anything finer anyway.
NOTION_POLL_INTERVAL_SECONDS = float(_env("NOTION_POLL_INTERVAL_SECONDS", "60"))
# Every Nth cycle enumerates everything instead of sweeping by watermark. It is
# the only way deletions are noticed: Notion reports neither a trash event nor a
# disconnection, so both are inferred from absence.
NOTION_RECONCILE_EVERY = int(_env("NOTION_RECONCILE_EVERY", "10"))
# The page fixtures are written under. Required for seeding only: an internal
# integration cannot create workspace-level pages, so a human-created, connected
# root is an input rather than something the seeder can make.
NOTION_ROOT_PAGE_ID = _env("NOTION_ROOT_PAGE_ID")

DSN = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"


def work_stream(partition: int) -> str:
    return f"stream:work:{partition}"


DLQ_STREAM = "stream:dlq"
CONSUMER_GROUP = "cg"

# Re-embed requests, one entity id per message. Unpartitioned: the writer reads
# current state from Postgres rather than trusting the message, so two workers
# racing on the same entity converge instead of interleaving stale text. See
# `embed.writer`.
EMBED_STREAM = "stream:embed"
EMBED_GROUP = "cg-embed"
EMBED_CONSUMER = _env("EMBED_CONSUMER", "embed-writer")

# Extraction jobs, one source entity id per message. Unpartitioned for the same
# reason as the embed stream and one more: the worker re-reads current state, so
# two consumers racing on one entity converge, and an LLM call is three orders of
# magnitude slower than a source apply. Tying it to the ingest partitions would
# let one busy channel stall extraction for everything sharing its partition.
SEMANTIC_STREAM = "stream:semantic"
SEMANTIC_GROUP = "cg-semantic"
SEMANTIC_CONSUMER = _env("SEMANTIC_CONSUMER", "semantic-0")
