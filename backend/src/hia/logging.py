"""Structured logging setup.

One call (:func:`configure_logging`) at process start; every module below just does
``structlog.get_logger(__name__)`` as normal. JSON output is what the add-on ships
with (machine-parseable, feeds the UI's log viewer later); console output is for local
development.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    """Configure structlog (and stdlib logging, which structlog wraps) once.

    Safe to call more than once (e.g. in tests) — later calls simply reconfigure.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
