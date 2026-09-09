"""Lead scoring (MVP section 20).

Scores are computed from the fields the AI actually captured, using rules
that an administrator can change at runtime — never hard-coded judgement.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.leads.models import ServiceType
from backend.scoring.defaults import DEFAULT_BANDS, DEFAULT_RULES
from backend.scoring.models import ScoringConfig

_TIMEFRAME_DAYS = {
    "immediate": 0,
    "asap": 0,
    "this_week": 7,
    "7_days": 7,
    "15_days": 15,
    "this_month": 30,
    "30_days": 30,
    "1_month": 30,
    "2_months": 60,
    "60_days": 60,
    "3_months": 90,
    "90_days": 90,
    "6_months": 180,
    "next_year": 365,
}


@dataclass
class ScoreResult:
    score: int
    max_score: int
    classification: str
    matched: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)


def _timeframe_to_days(value) -> int | None:
    """Turn '30_days', 'this month', '2 months' into a number of days."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if text in _TIMEFRAME_DAYS:
        return _TIMEFRAME_DAYS[text]
    match = re.search(r"(\d+)\s*_?(day|week|month|year)", text)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return amount * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
    if text.isdigit():
        return int(text)
    return None


def _rule_matches(rule: dict, collected: dict) -> bool:
    value = collected.get(rule.get("field"))
    op = rule.get("op", "truthy")

    if op == "truthy":
        return bool(value) and str(value).strip().lower() not in ("no", "false", "0")
    if value is None:
        return False
    if op == "eq":
        return str(value).lower() == str(rule.get("value")).lower()
    if op == "in":
        options = [str(v).lower() for v in rule.get("value", [])]
        return str(value).lower() in options
    if op in ("gte", "lte"):
        try:
            numeric = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return False
        threshold = float(rule.get("value", 0))
        return numeric >= threshold if op == "gte" else numeric <= threshold
    if op == "within_days":
        days = _timeframe_to_days(value)
        return days is not None and days <= float(rule.get("value", 0))
    return False


def get_config(db: Session, service: ServiceType) -> ScoringConfig:
    """Stored rules for a service, seeded from defaults on first use."""
    config = db.scalar(select(ScoringConfig).where(ScoringConfig.service == service))
    if config is None:
        config = ScoringConfig(
            service=service,
            rules=DEFAULT_RULES.get(service, []),
            bands=[list(band) for band in DEFAULT_BANDS],
        )
        db.add(config)
        db.flush()
    return config


def classify(score: int, bands: list) -> str:
    for minimum, label in sorted(bands, key=lambda b: -b[0]):
        if score >= minimum:
            return label
    return "UNQUALIFIED"


def score_lead(db: Session, service: ServiceType | None, collected: dict) -> ScoreResult:
    """Score what the conversation gathered."""
    if service is None:
        return ScoreResult(score=0, max_score=0, classification="UNQUALIFIED")

    config = get_config(db, service)
    rules = config.rules or []
    total = sum(int(rule.get("points", 0)) for rule in rules)

    earned = 0
    matched: list[str] = []
    missed: list[str] = []
    for rule in rules:
        label = rule.get("label") or rule.get("field", "rule")
        if _rule_matches(rule, collected or {}):
            earned += int(rule.get("points", 0))
            matched.append(label)
        else:
            missed.append(label)

    # Normalize to 0-100 so bands mean the same thing across services.
    normalized = round(earned / total * 100) if total else 0
    return ScoreResult(
        score=normalized,
        max_score=100,
        classification=classify(normalized, config.bands or DEFAULT_BANDS),
        matched=matched,
        missed=missed,
    )
