from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from tigr import (  # noqa: F401
    audit,
    connection_models,
    datasets,
    delivery,
    governance,
    iam,
    jobs,
    jobs_api,
    outbox_worker,
    scenarios,
    schema_records,
    source_mappings,
    source_queries,
    ssd_models,
    ticket_models,
)
from tigr.config import get_settings
from tigr.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, include_schemas=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_schemas=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
