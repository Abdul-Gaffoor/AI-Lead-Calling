import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.auth.dependencies import get_current_user, require_roles
from backend.auth.models import Role, User
from backend.core.database import get_db
from backend.leads.importer import TEMPLATE_COLUMNS, import_leads
from backend.leads.models import Lead, LeadRejection, LeadStatus, LeadUpload
from backend.leads.schemas import LeadWithCustomer, RejectionOut, UploadSummary

router = APIRouter(prefix="/leads", tags=["leads"])

_UPLOAD_ROLES = (Role.SUPER_ADMIN, Role.SALES_MANAGER, Role.LEAD_OPERATOR)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.get("/template", response_class=PlainTextResponse)
def download_template(_: User = Depends(get_current_user)):
    """CSV template with the required and optional columns."""
    return PlainTextResponse(
        ",".join(TEMPLATE_COLUMNS) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="swaraj_leads_template.csv"'},
    )


@router.post("/uploads", response_model=UploadSummary, status_code=status.HTTP_201_CREATED)
async def upload_leads(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*_UPLOAD_ROLES)),
):
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 10 MB limit")
    upload = import_leads(
        db, user=current_user, filename=file.filename or "upload.csv", content=content
    )
    return upload


@router.get("/uploads", response_model=list[UploadSummary])
def list_uploads(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_UPLOAD_ROLES)),
):
    return db.scalars(
        select(LeadUpload).order_by(LeadUpload.id.desc()).limit(limit).offset(offset)
    ).all()


@router.get("/uploads/{upload_id}", response_model=UploadSummary)
def get_upload(
    upload_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_UPLOAD_ROLES)),
):
    upload = db.get(LeadUpload, upload_id)
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    return upload


@router.get("/uploads/{upload_id}/rejections", response_model=list[RejectionOut])
def list_rejections(
    upload_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_UPLOAD_ROLES)),
):
    if db.get(LeadUpload, upload_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    return db.scalars(
        select(LeadRejection)
        .where(LeadRejection.upload_id == upload_id)
        .order_by(LeadRejection.row_number)
    ).all()


@router.get("/uploads/{upload_id}/rejections.csv")
def download_rejections_csv(
    upload_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(*_UPLOAD_ROLES)),
):
    """Downloadable rejection reasons (MVP section 3)."""
    if db.get(LeadUpload, upload_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    rejections = db.scalars(
        select(LeadRejection)
        .where(LeadRejection.upload_id == upload_id)
        .order_by(LeadRejection.row_number)
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Row", "Customer_Name", "Mobile_Number", "Reason", "Details"])
    for r in rejections:
        writer.writerow([r.row_number, r.customer_name, r.mobile_number, r.reason.value, r.details])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="upload_{upload_id}_rejections.csv"'
        },
    )


@router.get("", response_model=list[LeadWithCustomer])
def list_leads(
    lead_status: LeadStatus | None = Query(default=None, alias="status"),
    upload_id: int | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Lead).options(joinedload(Lead.customer)).order_by(Lead.id.desc())
    if lead_status is not None:
        stmt = stmt.where(Lead.status == lead_status)
    if upload_id is not None:
        stmt = stmt.where(Lead.upload_id == upload_id)
    leads = db.scalars(stmt.limit(limit).offset(offset)).all()
    return [
        LeadWithCustomer(
            **{k: getattr(lead, k) for k in LeadWithCustomer.model_fields if k not in ("customer_name", "customer_phone")},
            customer_name=lead.customer.name,
            customer_phone=lead.customer.phone,
        )
        for lead in leads
    ]
