import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.auth.models import User
from backend.compliance.models import AuditLog, SuppressionEntry
from backend.customers.models import Customer


def log_action(
    db: Session,
    *,
    actor: User | None,
    action: str,
    entity: str | None = None,
    details: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        entity=entity,
        details=details,
    )
    db.add(entry)
    return entry


def is_suppressed(db: Session, phone: str) -> bool:
    return (
        db.scalar(select(SuppressionEntry).where(SuppressionEntry.phone == phone)) is not None
    )


def add_suppression(
    db: Session,
    phone: str,
    *,
    reason: str = "OPT_OUT",
    source: str | None = None,
    actor: User | None = None,
) -> SuppressionEntry:
    """Add a phone to the suppression list and flag the customer, idempotently."""
    entry = db.scalar(select(SuppressionEntry).where(SuppressionEntry.phone == phone))
    if entry is None:
        entry = SuppressionEntry(phone=phone, reason=reason, source=source)
        db.add(entry)

    customer = db.scalar(select(Customer).where(Customer.phone == phone))
    if customer is not None and not customer.opted_out:
        customer.opted_out = True
        customer.opted_out_at = dt.datetime.now(dt.timezone.utc)

    log_action(
        db,
        actor=actor,
        action="OPT_OUT_RECORDED",
        entity=f"phone:{phone}",
        details={"reason": reason, "source": source},
    )
    return entry
