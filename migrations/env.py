import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context

# Load .env from project root
load_dotenv()

# Alembic Config object
config = context.config

# Override sqlalchemy.url with DATABASE_URL from environment
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

# Set up loggers
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import models for autogenerate support
from shared.db.models import Base

target_metadata = Base.metadata

# Tables managed by Alembic (defined in shared/db/models.py).
# Tables that exist in the DB but aren't in our models (e.g. users, boot_events)
# are ignored so autogenerate won't try to drop them.
MANAGED_TABLES = set(target_metadata.tables.keys())


def include_object(object, name, type_, reflected, compare_to):
    """Filter autogenerate to only track tables/columns defined in our models.

    Our SQLAlchemy models are a subset of the real DB schema. This filter
    prevents Alembic from trying to drop columns, indexes, constraints,
    or tables that exist in the DB but aren't in our models.
    """
    # Skip tables not in our models
    if type_ == "table":
        return name in MANAGED_TABLES

    # For anything else: if it exists in the DB (reflected) but has no
    # counterpart in our models (compare_to is None), ignore it.
    # This prevents "Detected removed column/index/constraint" noise.
    if reflected and compare_to is None:
        return False

    return True


def process_revision_directives(context, revision, directives):
    """Filter out nullable/FK-only noise from autogenerate.

    Since our models are a simplified view of the DB, autogenerate detects
    nullable and foreign key differences that aren't real migrations.
    Strip these so `alembic check` only flags genuine schema changes.
    """
    if not directives:
        return
    script = directives[0]
    if not script.upgrade_ops:
        return

    filtered = []
    for op in script.upgrade_ops.ops:
        # Keep add/drop column and add/drop table — those are real changes
        op_type = type(op).__name__
        if op_type in ("ModifyTableOps",):
            # ModifyTableOps wraps modify_nullable etc. — skip these
            continue
        if op_type == "CreateForeignKeyOp" or op_type == "DropForeignKeyOp":
            # FK diffs from model vs DB naming — skip
            continue
        filtered.append(op)

    script.upgrade_ops.ops = filtered


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=False,
        process_revision_directives=process_revision_directives,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=False,
            process_revision_directives=process_revision_directives,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
