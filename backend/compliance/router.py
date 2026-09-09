from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user, require_roles
from backend.auth.models import Role, User
from backend.compliance.models import SuppressionEntry
from backend.compliance.schemas import OptOutCreate, SuppressionOut
from backend.compliance.service import add_suppression
from backend.core.database import get_db
from backend.leads.phone import normalize_indian_mobile

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.post("/opt-outs", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
def create_opt_out(
    payload: OptOutCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    phone = normalize_indian_mobile(payload.phone)
    if phone is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a valid Indian mobile number")
    entry = add_suppression(
        db, phone, reason=payload.reason, source=payload.source, actor=current_user
    )
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/opt-outs", response_model=list[SuppressionOut])
def list_opt_outs(
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Role.SUPER_ADMIN, Role.SALES_MANAGER)),
):
    return db.scalars(
        select(SuppressionEntry).order_by(SuppressionEntry.id.desc()).limit(limit).offset(offset)
    ).all()
