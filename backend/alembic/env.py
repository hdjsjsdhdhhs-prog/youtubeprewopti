"""Alembic environment. Uses the migration (schema owner) URL from app settings."""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app.core.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Procrastinate owns its tables; they are created by a migration but not modelled in SQLAlchemy.
EXCLUDED_PREFIXES = ("procrastinate_",)


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    return not (type_ == "table" and name and name.startswith(EXCLUDED_PREFIXES))


def _url() -> str:
    override = config.attributes.get("database_url")
    return override or get_settings().migration_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      include_object=include_object, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          include_object=include_object, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
