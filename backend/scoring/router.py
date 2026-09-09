from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, require_roles
from backend.auth.models import Role, User
from backend.core.database import get_db
from backend.leads.models import ServiceType
from backend.scoring.schemas import (
    ScoreOut,
    ScoreRequest,
    ScoringConfigOut,
    ScoringConfigUpdate,
)
from backend.scoring.service import get_config, score_lead

router = APIRouter(prefix="/scoring", tags=["scoring"])


@router.post("/score", response_model=ScoreOut)
def score(
    payload: ScoreRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = score_lead(db, payload.service, payload.collected)
    db.commit()
    return asdict(result)


@router.get("/configs", response_model=list[ScoringConfigOut])
def list_configs(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Role.SUPER_ADMIN, Role.SALES_MANAGER)),
):
    configs = [get_config(db, service) for service in ServiceType]
    db.commit()
    return configs


@router.patch("/configs/{service}", response_model=ScoringConfigOut)
def update_config(
    service: ServiceType,
    payload: ScoringConfigUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Role.SUPER_ADMIN)),
):
    """Change scoring weights or bands (MVP section 20: admin-configurable)."""
    config = get_config(db, service)
    if payload.rules is not None:
        config.rules = payload.rules
    if payload.bands is not None:
        config.bands = payload.bands
    db.commit()
    db.refresh(config)
    return config
