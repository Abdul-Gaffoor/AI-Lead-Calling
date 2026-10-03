"""A throwaway database for the harness to run against.

The harness writes customers, leads, call attempts and dispositions. None
of that belongs in a real database, and `evaluate` is a command somebody
will eventually run on the production container to check a provider change.
So it never uses the configured database: it builds its own in memory,
every run, and throws it away afterwards.

That also makes the numbers reproducible. A shared database would carry
yesterday's suppressions into today's opt-out scores.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.database import Base


def _register_models() -> None:
    """Import every module that defines a table.

    `Base.metadata` only knows about models that have been imported, and a
    missing import shows up as "no such table" halfway through a run rather
    than as an import error.
    """
    from backend.ai import models as ai_models  # noqa: F401
    from backend.auth import models as auth_models  # noqa: F401
    from backend.calls import models as call_models  # noqa: F401
    from backend.campaigns import models as campaign_models  # noqa: F401
    from backend.compliance import models as compliance_models  # noqa: F401
    from backend.customers import models as customer_models  # noqa: F401
    from backend.knowledge import models as knowledge_models  # noqa: F401
    from backend.leads import models as lead_models  # noqa: F401
    from backend.quality import models as quality_models  # noqa: F401
    from backend.sales import models as sales_models  # noqa: F401
    from backend.scoring import models as scoring_models  # noqa: F401
    from backend.surveys import models as survey_models  # noqa: F401


def session_factory():
    """A sessionmaker bound to a fresh, empty, in-memory database."""
    _register_models()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # keeps the in-memory database alive between sessions
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
