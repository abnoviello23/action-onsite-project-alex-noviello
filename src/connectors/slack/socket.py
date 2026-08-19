"""Socket Mode transport.

An outbound WebSocket to Slack, so no public HTTPS, tunnel, or inbound port is
needed — which is why this works unchanged inside a container on a laptop.

Reconnects are routine, not exceptional: Slack proactively sends a `disconnect`
envelope every few minutes and expects the client to reopen with a fresh URL.
The loop below treats a closed socket as the normal path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets

log = logging.getLogger("connectors.slack.socket")

OPEN_URL = "https://slack.com/api/apps.connections.open"
MAX_BACKOFF_SECONDS = 30.0


class SocketModeClient:
    """Yields Events API payloads, acking each envelope as it goes.

    Requires the app-level token (`xapp-`), which is the only credential that
    can open this connection and cannot call the Web API at all.
    """

    def __init__(self, app_token: str) -> None:
        if not app_token.startswith("xapp-"):
            raise ValueError("Socket Mode requires an app-level token (xapp-)")
        self._token = app_token

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        """Reconnecting stream of event payloads. Runs until cancelled."""
        attempt = 0
        while True:
            try:
                url = await self._open_connection()
                attempt = 0
                async for payload in self._consume(url):
                    yield payload
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                delay = min(2.0**attempt, MAX_BACKOFF_SECONDS)
                log.warning("socket error (%s); reconnecting in %.0fs", exc, delay)
                await asyncio.sleep(delay)

    async def _open_connection(self) -> str:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                OPEN_URL, headers={"Authorization": f"Bearer {self._token}"}
            )
            resp.raise_for_status()
            body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"apps.connections.open failed: {body.get('error')}")
        return body["url"]

    async def _consume(self, url: str) -> AsyncIterator[dict[str, Any]]:
        async with websockets.connect(url, ping_interval=20) as ws:
            log.info("socket connected")
            async for raw in ws:
                envelope = json.loads(raw)
                kind = envelope.get("type")

                if kind == "hello":
                    continue
                if kind == "disconnect":
                    # Routine: Slack cycles connections. Returning re-enters the
                    # outer loop, which opens a fresh URL.
                    log.info("disconnect requested (%s)", envelope.get("reason"))
                    return

                # Slack redelivers anything unacked within ~3s, so acking comes
                # first and is unconditional. A handler that throws must not
                # stall the socket or trigger a redelivery loop; the backfill
                # poller is the safety net for events lost that way.
                if envelope_id := envelope.get("envelope_id"):
                    await ws.send(json.dumps({"envelope_id": envelope_id}))

                if kind == "events_api":
                    event = (envelope.get("payload") or {}).get("event")
                    if event:
                        yield event
