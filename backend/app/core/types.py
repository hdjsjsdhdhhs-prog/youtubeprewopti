"""Shared column types."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def pg_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Native PostgreSQL enum storing the lowercase ``.value`` of a StrEnum."""
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
        validate_strings=True,
    )
