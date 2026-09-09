"""Default lead-scoring rules (MVP section 20).

Each rule awards points when a collected field satisfies a condition. Every
weight is admin-configurable at runtime; these are only the starting values,
matching the worked residential example in the MVP.
"""

from backend.leads.models import ServiceType

#: Rule shape: {"field": ..., "op": ..., "value": ..., "points": ...}
#: Operators: truthy, gte, lte, eq, in, within_days
RESIDENTIAL_RULES = [
    {"field": "property_owned", "op": "truthy", "points": 15, "label": "Owns property"},
    {"field": "roof_available", "op": "truthy", "points": 15, "label": "Roof available"},
    {"field": "monthly_bill", "op": "gte", "value": 2500, "points": 15,
     "label": "Suitable electricity usage"},
    {"field": "installation_timeline", "op": "within_days", "value": 30, "points": 20,
     "label": "Installation within 30 days"},
    {"field": "site_survey", "op": "truthy", "points": 20, "label": "Site survey agreed"},
    {"field": "decision_maker", "op": "truthy", "points": 15, "label": "Decision maker"},
]

COMMERCIAL_RULES = [
    {"field": "premises_owned", "op": "truthy", "points": 15, "label": "Owns premises"},
    {"field": "roof_or_land_available", "op": "truthy", "points": 15, "label": "Space available"},
    {"field": "monthly_bill", "op": "gte", "value": 25000, "points": 20,
     "label": "Substantial consumption"},
    {"field": "decision_maker", "op": "truthy", "points": 20, "label": "Decision maker"},
    {"field": "timeline", "op": "within_days", "value": 60, "points": 15,
     "label": "Timeline within 60 days"},
    {"field": "site_survey", "op": "truthy", "points": 15, "label": "Technical survey agreed"},
]

INDUSTRIAL_RULES = [
    {"field": "monthly_bill", "op": "gte", "value": 100000, "points": 25,
     "label": "Large consumption"},
    {"field": "roof_or_land_available", "op": "truthy", "points": 20, "label": "Space available"},
    {"field": "decision_maker", "op": "truthy", "points": 20, "label": "Decision maker"},
    {"field": "timeline", "op": "within_days", "value": 90, "points": 15,
     "label": "Timeline within 90 days"},
    {"field": "site_survey", "op": "truthy", "points": 20, "label": "Technical survey agreed"},
]

AGRICULTURE_RULES = [
    {"field": "land_ownership", "op": "truthy", "points": 25, "label": "Owns land"},
    {"field": "land_area", "op": "truthy", "points": 15, "label": "Land area known"},
    {"field": "pump_hp", "op": "truthy", "points": 20, "label": "Pump capacity known"},
    {"field": "scheme_interest", "op": "truthy", "points": 20, "label": "Interested in scheme"},
    {"field": "site_survey", "op": "truthy", "points": 20, "label": "Site survey agreed"},
]

SERVICE_RULES = [
    {"field": "existing_capacity", "op": "truthy", "points": 25, "label": "System capacity known"},
    {"field": "issue", "op": "truthy", "points": 30, "label": "Issue identified"},
    {"field": "location", "op": "truthy", "points": 20, "label": "Location known"},
    {"field": "urgency", "op": "truthy", "points": 25, "label": "Urgency captured"},
]

DEFAULT_RULES: dict[ServiceType, list[dict]] = {
    ServiceType.RESIDENTIAL_SOLAR: RESIDENTIAL_RULES,
    ServiceType.PM_SURYA_GHAR: RESIDENTIAL_RULES,
    ServiceType.COMMERCIAL_SOLAR: COMMERCIAL_RULES,
    ServiceType.INDUSTRIAL_SOLAR: INDUSTRIAL_RULES,
    ServiceType.GROUND_MOUNTED_SOLAR: COMMERCIAL_RULES,
    ServiceType.AGRICULTURE_SOLAR: AGRICULTURE_RULES,
    ServiceType.EXISTING_SOLAR_UPGRADE: RESIDENTIAL_RULES,
    ServiceType.SOLAR_MAINTENANCE: SERVICE_RULES,
    ServiceType.PANEL_CLEANING: SERVICE_RULES,
    ServiceType.GENERAL_ENQUIRY: [
        {"field": "location", "op": "truthy", "points": 30, "label": "Location known"},
        {"field": "interest", "op": "truthy", "points": 40, "label": "Interest captured"},
        {"field": "site_survey", "op": "truthy", "points": 30, "label": "Survey agreed"},
    ],
}

#: Classification bands (MVP section 20): (minimum_score, label)
DEFAULT_BANDS = [(80, "HOT"), (50, "WARM"), (20, "COLD"), (0, "UNQUALIFIED")]
