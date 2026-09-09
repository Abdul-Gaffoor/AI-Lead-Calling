from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import settings
from backend.core.database import Base, engine

# Import all model modules so Base.metadata is complete before create_all /
# Alembic autogenerate.
from backend.auth import models as auth_models  # noqa: F401
from backend.calls import models as call_models  # noqa: F401
from backend.campaigns import models as campaign_models  # noqa: F401
from backend.compliance import models as compliance_models  # noqa: F401
from backend.customers import models as customer_models  # noqa: F401
from backend.leads import models as lead_models  # noqa: F401

from backend.auth.router import router as auth_router
from backend.calls.router import router as calls_router
from backend.campaigns.router import router as campaigns_router
from backend.compliance.router import router as compliance_router
from backend.customers.router import router as customers_router
from backend.leads.router import router as leads_router


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(leads_router)
    app.include_router(campaigns_router)
    app.include_router(calls_router)
    app.include_router(customers_router)
    app.include_router(compliance_router)

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok"}

    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)

    return app


app = create_app()
