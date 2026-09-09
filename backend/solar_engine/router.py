from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status

from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.solar_engine.calculator import estimate
from backend.solar_engine.schemas import EstimateOut, EstimateRequest

router = APIRouter(prefix="/solar", tags=["solar engine"])


@router.post("/estimate", response_model=EstimateOut)
def solar_estimate(
    payload: EstimateRequest,
    _: User = Depends(get_current_user),
):
    """Indicative system size, generation, savings and payback.

    The single approved calculation used by the website, the AI agent and
    sales executives (MVP section 19).
    """
    try:
        result = estimate(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return asdict(result)
