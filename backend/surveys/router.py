from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.compliance.service import log_action
from backend.core.database import get_db
from backend.leads.models import Lead
from backend.surveys.models import SiteSurvey, SurveyStatus
from backend.surveys.schemas import SurveyCreate, SurveyOut, SurveyUpdate

router = APIRouter(prefix="/surveys", tags=["site surveys"])


@router.post("", response_model=SurveyOut, status_code=status.HTTP_201_CREATED)
def create_survey(
    payload: SurveyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    customer_id = payload.customer_id
    if customer_id is None:
        if payload.lead_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide lead_id or customer_id"
            )
        lead = db.get(Lead, payload.lead_id)
        if lead is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")
        customer_id = lead.customer_id

    survey = SiteSurvey(**payload.model_dump(exclude={"customer_id"}), customer_id=customer_id)
    db.add(survey)
    db.flush()
    log_action(db, actor=current_user, action="SURVEY_CREATED", entity=f"survey:{survey.id}")
    db.commit()
    db.refresh(survey)
    return survey


@router.get("", response_model=list[SurveyOut])
def list_surveys(
    survey_status: SurveyStatus | None = Query(default=None, alias="status"),
    assigned_to_id: int | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(SiteSurvey).order_by(SiteSurvey.id.desc())
    if survey_status is not None:
        stmt = stmt.where(SiteSurvey.status == survey_status)
    if assigned_to_id is not None:
        stmt = stmt.where(SiteSurvey.assigned_to_id == assigned_to_id)
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/{survey_id}", response_model=SurveyOut)
def get_survey(
    survey_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    survey = db.get(SiteSurvey, survey_id)
    if survey is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey not found")
    return survey


@router.patch("/{survey_id}", response_model=SurveyOut)
def update_survey(
    survey_id: int,
    payload: SurveyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    survey = db.get(SiteSurvey, survey_id)
    if survey is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey not found")

    changes = payload.model_dump(exclude_unset=True)
    # Assigning an engineer moves a scheduled survey along automatically.
    if changes.get("assigned_to_id") and survey.status in (
        SurveyStatus.REQUESTED,
        SurveyStatus.SCHEDULED,
    ):
        changes.setdefault("status", SurveyStatus.ASSIGNED)
    if changes.get("scheduled_at") and survey.status is SurveyStatus.REQUESTED:
        changes.setdefault("status", SurveyStatus.SCHEDULED)

    for field, value in changes.items():
        setattr(survey, field, value)

    log_action(
        db,
        actor=current_user,
        action="SURVEY_UPDATED",
        entity=f"survey:{survey.id}",
        details={"status": survey.status.value},
    )
    db.commit()
    db.refresh(survey)
    return survey
