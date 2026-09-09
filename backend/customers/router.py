from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.customers.models import Customer
from backend.customers.schemas import CustomerDetail, CustomerOut
from backend.core.database import get_db
from backend.leads.phone import normalize_indian_mobile

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerOut])
def search_customers(
    phone: str | None = Query(default=None, description="Phone number in any common format"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Customer).order_by(Customer.id)
    if phone:
        normalized = normalize_indian_mobile(phone)
        stmt = stmt.where(Customer.phone == (normalized or phone))
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/{customer_id}", response_model=CustomerDetail)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .options(selectinload(Customer.leads))
    )
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return customer
