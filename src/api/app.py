"""API worker: serves retrieval requests with permission filtering.

Access resolution walks *up* from candidate nodes (bounded by tree depth) rather
than down from grants (unbounded subtree). The principal set is computed once
per session and cached. That path lives in `query.visibility` and is reached
through `/agent/query`.

The /graph routes mounted here are the exception to all of that: they are an
operator's view of what has been ingested, deliberately unfiltered by principal,
and they are why the CORS allowlist below is localhost-only.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from actions import Runner
from agent.client import MessagesClient
from api.actions import router as actions_router
from api.agent import router as agent_router
from api.graph import router as graph_router
from common import config, db
from common.logging import setup
from core.registry import SEMANTIC_TYPES
from embed import ChunkerClient
from semantic.registry import ActiveConfig
from semantic.registry import load as load_ontology

log = setup("api")

# The Vite dev server. Same-origin in production — the visualizer is served from
# its own port only while it is a dev tool.
DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One pool and one of each upstream client, for the process.

    Opened here rather than per-request because a graph render is four queries
    in a row and an agent run is dozens; paying a connection handshake for each
    would dominate them. The HTTP clients hold their own connection pools, which
    is exactly what should be shared rather than rebuilt per request.
    """
    app.state.pool = await db.pool()

    # The semantic vocabulary has to be registered before the first request, or
    # `query_type('person')` is rejected as an unknown type and the agent's
    # schema digest silently omits half the graph. `ActiveConfig` picks up a
    # published revision from then on without a restart.
    app.state.ontology = ActiveConfig()
    async with app.state.pool.acquire() as conn:
        await load_ontology(conn)

    app.state.chunker = ChunkerClient(config.EMBED_URL)
    await app.state.chunker.__aenter__()

    # The agent is optional. Without a key the rest of the API — /health and the
    # operator canvas — still serves, and /agent/query answers 503 rather than
    # the process refusing to boot.
    app.state.messages = None
    if config.ANTHROPIC_API_KEY:
        app.state.messages = MessagesClient(
            base_url=config.ANTHROPIC_BASE_URL,
            api_key=config.ANTHROPIC_API_KEY,
            version=config.ANTHROPIC_VERSION,
            model=config.ANTHROPIC_MODEL,
        )
        await app.state.messages.__aenter__()
        log.info("agent enabled; model=%s", config.ANTHROPIC_MODEL)
    else:
        log.warning("ANTHROPIC_API_KEY unset; /agent/query disabled")

    # Held open for the process: each writer carries its own connection pool,
    # and the Drive one carries a service-account token with a lifetime, so
    # rebuilding them per request would pay a full auth handshake to post one
    # message. None when actions are off, which is what /actions checks.
    app.state.actions = None
    if config.ACTIONS_ENABLED:
        app.state.actions = Runner()
        log.warning("ACTIONS_ENABLED; this process can write to Slack/Drive/Notion")

    if not config.AGENT_DEMO_IDENTITIES:
        log.warning("AGENT_DEMO_IDENTITIES unset; the demo identity header is off")

    log.info("connected to postgres; %d partitions", config.NUM_PARTITIONS)
    try:
        yield
    finally:
        if app.state.actions is not None:
            await app.state.actions.aclose()
        if app.state.messages is not None:
            await app.state.messages.__aexit__(None, None, None)
        await app.state.chunker.__aexit__(None, None, None)
        await app.state.pool.close()


app = FastAPI(title="Permissioned Knowledge Graph API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(graph_router)
app.include_router(agent_router)
app.include_router(actions_router)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "partitions": config.NUM_PARTITIONS,
        "agent": getattr(app.state, "messages", None) is not None,
        "semantic_types": sorted(SEMANTIC_TYPES),
        "actions": config.ACTIONS_ENABLED,
    }
