import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import settings
from backend.core.logging_filters import install as install_pii_filter
from backend.core.observability import Timer, metrics, record_request, setup_tracing
from backend.core.ratelimit import enforce
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
from backend.knowledge import models as knowledge_models  # noqa: F401
from backend.quality import models as quality_models  # noqa: F401
from backend.customers import models as customer_models  # noqa: F401
from backend.leads import models as lead_models  # noqa: F401

from backend.ai.router import router as ai_router
from backend.ai.media_router import router as media_router
from backend.auth.router import router as auth_router
from backend.calls.router import router as calls_router
from backend.campaigns.router import router as campaigns_router
from backend.compliance.router import router as compliance_router
from backend.customers.router import router as customers_router
from backend.knowledge.router import router as knowledge_router
from backend.leads.public_router import router as public_router
from backend.quality.router import router as quality_router
from backend.leads.router import router as leads_router
from backend.reports.router import router as reports_router
from backend.sales.router import router as sales_router
from backend.scoring.router import router as scoring_router
from backend.solar_engine.router import router as solar_router
from backend.surveys.router import router as surveys_router


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0")

    # MVP §32: mask personal data in anything this process logs. Installed
    # on the handlers rather than a logger, so a leak from any module —
    # including a third-party library's own logging — is covered.
    if settings.mask_pii_in_logs:
        install_pii_filter()

    @app.middleware("http")
    async def observe(request: Request, call_next):
        """Time every request (MVP §33).

        Labelled with the route template, never the resolved path: a label
        of /calls/8213 would create one time series per call.
        """
        with Timer() as timer:
            response = await call_next(request)
        route = request.scope.get("route")
        record_request(
            request.method,
            getattr(route, "path", request.url.path),
            response.status_code,
            timer.seconds,
        )
        return response

    @app.middleware("http")
    async def rate_limit(request, call_next):
        """MVP §32. Login is limited separately and much harder.

        Middleware runs outside FastAPI's exception handlers, so an
        HTTPException raised here would escape as a 500 rather than
        becoming a 429. The response is built directly instead.
        """
        path = request.url.path
        try:
            if path.startswith("/auth/login"):
                enforce(
                    request,
                    limit=settings.login_rate_limit_per_minute,
                    window_s=settings.rate_limit_window_s,
                    scope="login",
                )
            elif not path.startswith(("/health", "/static", "/assets")):
                # Health is exempt so a rate-limited API still reports its
                # state to the deploy check and to Docker.
                enforce(
                    request,
                    limit=settings.rate_limit_per_minute,
                    window_s=settings.rate_limit_window_s,
                    scope="api",
                )
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers or {},
            )
        return await call_next(request)

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
    app.include_router(media_router)
    app.include_router(customers_router)
    app.include_router(compliance_router)
    app.include_router(knowledge_router)
    app.include_router(scoring_router)
    app.include_router(solar_router)
    app.include_router(surveys_router)
    app.include_router(sales_router)
    app.include_router(reports_router)
    app.include_router(quality_router)
    app.include_router(public_router)

    # MVP §33. Says so plainly if it was asked for and could not start:
    # absent spans look exactly like absent traffic.
    setup_tracing(app)

    @app.get("/metrics", tags=["system"], include_in_schema=False)
    def prometheus_metrics(request: Request):
        """Prometheus scrape endpoint (MVP §33).

        Off unless METRICS_ENABLED. When on, it is reachable from
        loopback, or from anywhere with METRICS_TOKEN — the series names
        alone describe the business (how many calls, how many opt-outs),
        so this is not public even though it carries no personal data.
        """
        if not settings.metrics_enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Metrics are not enabled")

        if settings.metrics_token:
            supplied = request.query_params.get("token") or request.headers.get(
                "x-metrics-token"
            )
            if supplied != settings.metrics_token:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid metrics token")
        elif (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Set METRICS_TOKEN to scrape from outside the host",
            )

        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok"}

    # Operations console. Served by the API so there is one URL to open and
    # no separate build step or container for the MVP.
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.is_dir():
        app.mount("/app", StaticFiles(directory=frontend_dir), name="console")

        def asset_version() -> str:
            """Fingerprint of the console assets, used to bust browser caches.

            Without this a browser keeps serving the previous deploy's CSS and
            JS from cache, so a fix appears not to have shipped.
            """
            digest = hashlib.sha256()
            for name in ("styles.css", "app.js"):
                asset = frontend_dir / name
                if asset.is_file():
                    digest.update(asset.read_bytes())
            return digest.hexdigest()[:12]

        @app.get("/", include_in_schema=False)
        def console():
            version = asset_version()
            html = (frontend_dir / "index.html").read_text(encoding="utf-8")
            html = html.replace("/app/styles.css", f"/app/styles.css?v={version}")
            html = html.replace("/app/app.js", f"/app/app.js?v={version}")
            # The page itself must never be cached, or the versioned asset
            # links inside it would be stale too.
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)

    return app


app = create_app()
