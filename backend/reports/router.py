import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.core.database import get_db
from backend.reports.service import call_metrics, daily_dashboard, funnel
from backend.reports.success import success_criteria

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/dashboard")
def dashboard(
    day: dt.date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Today's numbers for the manager (MVP section 27)."""
    return daily_dashboard(db, day)


@router.get("/funnel")
def sales_funnel(
    since: dt.date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Lead-to-order funnel with conversion rates (MVP section 28)."""
    return funnel(db, since)


@router.get("/calls")
def calls_report(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return call_metrics(db)


@router.get("/success-criteria")
def success(
    since: dt.date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """MVP §38, measured rather than asserted.

    Every derived figure carries the assumptions behind it, and a measure
    with no data says so instead of reporting zero.
    """
    return success_criteria(db, since=since)
