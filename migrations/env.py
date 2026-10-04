"""Alembic env — URL lấy từ DATABASE_URL (cùng biến với api/worker), metadata từ core.models."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from core.config import get_settings
from core.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata

# Bảng nội bộ của Apache AGE (schema ag_catalog + schema của graph) không do Alembic quản lý.
_EXCLUDED_SCHEMAS = {"ag_catalog", "retail_kb"}


def include_object(obj, name, type_, reflected, compare_to):  # pragma: no cover - chỉ chạy lúc autogenerate
    schema = getattr(obj, "schema", None)
    if schema in _EXCLUDED_SCHEMAS:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"}, include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
