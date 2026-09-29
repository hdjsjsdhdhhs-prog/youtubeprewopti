"""Structured logging (structlog → JSON on stdout)."""

from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if s.log_json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=level, stream=sys.stdout, format="%(levelname)s %(name)s %(message)s")


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
