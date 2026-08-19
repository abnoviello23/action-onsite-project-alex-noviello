"""Logging setup shared by every service.

basicConfig is a no-op after the first call, so the logger name goes in the
format string via %(name)s rather than being baked in per-call — otherwise every
logger in a process renders under whichever module happened to configure first.
"""

import logging

from common import config


def setup(service: str) -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(service)
