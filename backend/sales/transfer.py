"""Handing a live call to a sales executive (MVP section 23).

    AI detects HUMAN_REQUEST
             ↓
    Check executive availability
             ↓
      Live Transfer  ──── nobody free ───▶  PRIORITY CALLBACK

The fallback is the important half. A customer who asked for a person and
got silence is worse off than one who was never called: they asked, the
system acknowledged, and then nothing happened. So every path through
this module ends in either a connected executive or a callback that
somebody will see, and the customer is told which.

Availability is explicit (`available_for_transfer`) rather than inferred
from `is_active`, because "may sign in to the console" and "is at their
desk able to take a call right now" are different questions, and guessing
the second from the first means transferring calls into voicemail.
"""

from __future__ import annotations

import datetime as dt
import enum
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth.models import Role, User
from backend.calls.models import CallAttempt, Disposition
from backend.calls.service import record_disposition, utcnow
from backend.compliance.service import log_action
from backend.sales.models import Opportunity, OpportunityStage
from backend.telephony.base import TelephonyError
from backend.telephony.factory import get_telephony_provider

logger = logging.getLogger(__name__)

#: How soon a customer who asked for a person should be called back when
#: nobody was free. Short, because they asked for a person now.
PRIORITY_CALLBACK_MINUTES = 15

#: Escalations that should reach a senior person rather than the next
#: executive in the queue (MVP §15, §23).
SENIOR_ROLE = Role.SALES_MANAGER


class Outcome(str, enum.Enum):
    CONNECTED = "CONNECTED"
    CALLBACK = "CALLBACK"


@dataclass
class TransferResult:
    outcome: Outcome
    executive: User | None
    callback_at: dt.datetime | None = None
    reason: str | None = None

    @property
    def connected(self) -> bool:
        return self.outcome is Outcome.CONNECTED


def available_executives(db: Session, *, role: Role = Role.SALES_EXECUTIVE) -> list[User]:
    """Executives who can actually take a call right now.

    Needs a phone number as well as the flag: an executive marked
    available with no number is a transfer that fails at the provider.
    """
    return list(
        db.scalars(
            select(User).where(
                User.role == role,
                User.is_active.is_(True),
                User.available_for_transfer.is_(True),
                User.phone.isnot(None),
                User.phone != "",
            )
        ).all()
    )


def pick_executive(db: Session, *, senior: bool = False) -> User | None:
    """The least-loaded available executive, or None.

    Load is open opportunities, matching how `assign_least_loaded` shares
    work out, so a transfer does not pile a live call onto whoever is
    already busiest.

    A senior escalation tries managers first and falls back to the normal
    queue: a large industrial lead reaching *somebody* beats it reaching
    nobody because every manager is on a call.
    """
    candidates: list[User] = []
    if senior:
        candidates = available_executives(db, role=SENIOR_ROLE)
    if not candidates:
        candidates = available_executives(db)
    if not candidates:
        return None

    open_counts = dict(
        db.execute(
            select(Opportunity.assigned_to_id, func.count(Opportunity.id))
            .where(
                Opportunity.stage.notin_((OpportunityStage.WON, OpportunityStage.LOST)),
                Opportunity.assigned_to_id.isnot(None),
            )
            .group_by(Opportunity.assigned_to_id)
        ).all()
    )
    return min(candidates, key=lambda user: (open_counts.get(user.id, 0), user.id))


def transfer_to_executive(
    db: Session,
    attempt: CallAttempt,
    *,
    senior: bool = False,
    summary: str | None = None,
    actor: User | None = None,
) -> TransferResult:
    """Connect this live call to a person, or book a priority callback."""
    executive = pick_executive(db, senior=senior)

    if executive is None:
        return _priority_callback(
            db, attempt, reason="no executive available", summary=summary, actor=actor
        )

    try:
        get_telephony_provider().transfer(attempt.provider_call_id or "", executive.phone or "")
    except TelephonyError as exc:
        # The provider refused. The customer is still on the line and still
        # wants a person, so this becomes a callback rather than an error.
        logger.warning(
            "Transfer of call %s to executive %s failed: %s",
            attempt.id, executive.id, exc,
        )
        return _priority_callback(
            db, attempt, reason=str(exc), summary=summary, actor=actor
        )

    log_action(
        db,
        actor=actor,
        action="call.transferred",
        entity=f"call:{attempt.id}",
        details={"executive_id": executive.id, "senior": senior},
    )
    logger.info("Call %s transferred to executive %s", attempt.id, executive.id)
    return TransferResult(outcome=Outcome.CONNECTED, executive=executive)


def _priority_callback(
    db: Session,
    attempt: CallAttempt,
    *,
    reason: str,
    summary: str | None,
    actor: User | None,
) -> TransferResult:
    """Nobody could take it: book the callback MVP §23 asks for."""
    callback_at = utcnow() + dt.timedelta(minutes=PRIORITY_CALLBACK_MINUTES)

    record_disposition(
        db,
        attempt,
        Disposition.CALLBACK_REQUESTED,
        summary=summary or "Customer asked for a person; none was available.",
        callback_at=callback_at,
        actor=actor,
    )
    log_action(
        db,
        actor=actor,
        action="call.transfer_failed",
        entity=f"call:{attempt.id}",
        details={"reason": reason, "callback_at": callback_at.isoformat()},
    )
    logger.info(
        "Call %s could not be transferred (%s); callback booked for %s",
        attempt.id, reason, callback_at,
    )
    return TransferResult(
        outcome=Outcome.CALLBACK,
        executive=None,
        callback_at=callback_at,
        reason=reason,
    )
