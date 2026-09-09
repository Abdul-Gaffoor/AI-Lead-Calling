from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, require_roles
from backend.auth.models import Role, User
from backend.campaigns.dispatcher import dispatch_campaign
from backend.campaigns.models import Campaign, CampaignLead, CampaignStatus
from backend.campaigns.schemas import (
    AddLeadsRequest,
    AddLeadsResult,
    CampaignCreate,
    CampaignDetail,
    CampaignLeadOut,
    CampaignOut,
    CampaignUpdate,
    DispatchResult,
)
from backend.campaigns.service import (
    CampaignStateError,
    add_leads,
    campaign_stats,
    transition,
)
from backend.core.database import get_db

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

_MANAGE_ROLES = (Role.SUPER_ADMIN, Role.SALES_MANAGER)


def _get_campaign(db: Session, campaign_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    return campaign


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    if payload.window_start == payload.window_end:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Calling window start and end must differ",
        )
    campaign = Campaign(
        **payload.model_dump(exclude={"retry_rules"}),
        retry_rules=payload.retry_rules,
        created_by_id=current_user.id,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.get("", response_model=list[CampaignOut])
def list_campaigns(
    campaign_status: CampaignStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Campaign).order_by(Campaign.id.desc())
    if campaign_status is not None:
        stmt = stmt.where(Campaign.status == campaign_status)
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/{campaign_id}", response_model=CampaignDetail)
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    campaign = _get_campaign(db, campaign_id)
    return CampaignDetail(
        **CampaignOut.model_validate(campaign, from_attributes=True).model_dump(),
        stats=campaign_stats(db, campaign_id),
    )


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    campaign = _get_campaign(db, campaign_id)
    if campaign.status in (CampaignStatus.STOPPED, CampaignStatus.COMPLETED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A finished campaign can no longer be edited"
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/leads", response_model=AddLeadsResult)
def add_campaign_leads(
    campaign_id: int,
    payload: AddLeadsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*_MANAGE_ROLES, Role.LEAD_OPERATOR)),
):
    campaign = _get_campaign(db, campaign_id)
    if campaign.status in (CampaignStatus.STOPPED, CampaignStatus.COMPLETED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cannot add leads to a finished campaign"
        )
    result = add_leads(
        db,
        campaign,
        lead_ids=payload.lead_ids,
        upload_id=payload.upload_id,
        actor=current_user,
    )
    db.commit()
    return result


@router.get("/{campaign_id}/queue", response_model=list[CampaignLeadOut])
def campaign_queue(
    campaign_id: int,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    _get_campaign(db, campaign_id)
    return db.scalars(
        select(CampaignLead)
        .where(CampaignLead.campaign_id == campaign_id)
        .order_by(CampaignLead.id)
        .limit(limit)
        .offset(offset)
    ).all()


def _transition_endpoint(target: CampaignStatus):
    def endpoint(
        campaign_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_roles(*_MANAGE_ROLES)),
    ):
        campaign = _get_campaign(db, campaign_id)
        try:
            transition(db, campaign, target, actor=current_user)
        except CampaignStateError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        db.commit()
        db.refresh(campaign)
        return campaign

    return endpoint


router.add_api_route(
    "/{campaign_id}/start",
    _transition_endpoint(CampaignStatus.RUNNING),
    methods=["POST"],
    response_model=CampaignOut,
    name="start_campaign",
)
router.add_api_route(
    "/{campaign_id}/pause",
    _transition_endpoint(CampaignStatus.PAUSED),
    methods=["POST"],
    response_model=CampaignOut,
    name="pause_campaign",
)
router.add_api_route(
    "/{campaign_id}/resume",
    _transition_endpoint(CampaignStatus.RUNNING),
    methods=["POST"],
    response_model=CampaignOut,
    name="resume_campaign",
)
router.add_api_route(
    "/{campaign_id}/stop",
    _transition_endpoint(CampaignStatus.STOPPED),
    methods=["POST"],
    response_model=CampaignOut,
    name="stop_campaign",
)
router.add_api_route(
    "/{campaign_id}/complete",
    _transition_endpoint(CampaignStatus.COMPLETED),
    methods=["POST"],
    response_model=CampaignOut,
    name="complete_campaign",
)


@router.post("/{campaign_id}/dispatch", response_model=DispatchResult)
def dispatch_now(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_MANAGE_ROLES)),
):
    """Run one dispatch tick immediately.

    The worker does this on a schedule; this endpoint is for operators and
    for verifying a campaign end to end.
    """
    campaign = _get_campaign(db, campaign_id)
    return DispatchResult(calls_placed=dispatch_campaign(db, campaign))
