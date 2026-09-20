from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.core.config import settings
from backend.core.database import Base

# NOTE for new migrations: PostgreSQL enums are database-wide objects, so a
# migration that adds a column reusing an existing enum (service_type is the
# one shared across tables here) must NOT emit CREATE TYPE for it again —
# autogenerate always will, and it fails on a real database. Reference it with
# postgresql.ENUM(..., create_type=False) instead; see the campaigns and AI
# migrations for the pattern. The CI "Verify migrations on PostgreSQL" job
# applies revisions one at a time and catches this.

# Import all model modules so autogenerate sees the full metadata.
from backend.ai import models as ai_models  # noqa: F401
from backend.auth import models as auth_models  # noqa: F401
from backend.calls import models as call_models  # noqa: F401
from backend.campaigns import models as campaign_models  # noqa: F401
from backend.compliance import models as compliance_models  # noqa: F401
from backend.sales import models as sales_models  # noqa: F401
from backend.scoring import models as scoring_models  # noqa: F401
from backend.surveys import models as survey_models  # noqa: F401
from backend.knowledge import models as knowledge_models  # noqa: F401
from backend.customers import models as customer_models  # noqa: F401
from backend.leads import models as lead_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
