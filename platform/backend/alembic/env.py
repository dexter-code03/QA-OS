from logging.config import fileConfig

from alembic import context

from app.db import engine
from app.models import Base
from app.settings import ensure_dirs

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

ensure_dirs()
config.set_main_option("sqlalchemy.url", str(engine.url))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
