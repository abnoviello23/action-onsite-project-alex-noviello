"""Run the BGE chunker HTTP service."""

from __future__ import annotations

import os

import uvicorn

from common.logging import setup

setup("embed")


def main() -> None:
    port = int(os.environ.get("EMBED_PORT", "8080"))
    uvicorn.run(
        "embed.service:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
