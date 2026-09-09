from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import settings
from backend.core.database import Base, engine

# Import all model modules so Base.metadata is complete before create_all /
# Alembic autogenerate.
from backend.ai import models as ai_models  # noqa: F401
from backend.auth import models as auth_models  # noqa: F401
from backend.calls import models as call_models  # noqa: F401
from backend.campaigns import models as campaign_models  # noqa: F401
from backend.compliance import models as compliance_models  # noqa: F401
from backend.sales import models as sales_models  # noqa: F401
from backend.scoring import models as scoring_models  # noqa: F401
from backend.surveys import models as survey_models  # noqa: F401
from backend.customers import models as customer_models  # noqa: F401
from backend.leads import models as lead_models  # noqa: F401

from backend.ai.router import router as ai_router
from backend.auth.router import router as auth_router
from backend.calls.router import router as calls_router
from backend.campaigns.router import router as campaigns_router
from backend.compliance.router import router as compliance_router
from backend.customers.router import router as customers_router
from backend.leads.public_router import router as public_router
from backend.leads.router import router as leads_router
from backend.reports.router import router as reports_router
from backend.sales.router import router as sales_router
from backend.scoring.router import router as scoring_router
from backend.solar_engine.router import router as solar_router
from backend.surveys.router import router as surveys_router


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
    app.include_router(ai_router)
    app.include_router(customers_router)
    app.include_router(compliance_router)
    app.include_router(scoring_router)
    app.include_router(solar_router)
    app.include_router(surveys_router)
    app.include_router(sales_router)
    app.include_router(reports_router)
    app.include_router(public_router)

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok"}

    # Operations console. Served by the API so there is one URL to open and
    # no separate build step or container for the MVP.
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.is_dir():
        app.mount("/app", StaticFiles(directory=frontend_dir), name="console")

        @app.get("/", include_in_schema=False)
        def console():
            return FileResponse(frontend_dir / "index.html")

    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)

    return app


app = create_app()
