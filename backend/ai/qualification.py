"""Qualification field requirements per service (MVP sections 12-17).

Sprint 3 establishes the framework and the residential/PM Surya Ghar sets;
the remaining services are filled out in Sprints 4 and 5.
"""

from backend.leads.models import ServiceType

#: Fields the AI should gather, per service. Order matters — it is the order
#: the AI is nudged to ask in.
REQUIRED_FIELDS: dict[ServiceType, list[str]] = {
    ServiceType.RESIDENTIAL_SOLAR: [
        "location",
        "property_owned",
        "property_type",
        "monthly_bill",
        "roof_available",
        "installation_timeline",
        "site_survey",
    ],
    ServiceType.PM_SURYA_GHAR: [
        "location",
        "property_owned",
        "electricity_connection",
        "roof_available",
        "monthly_bill",
        "subsidy_interest",
        "site_survey",
    ],
    ServiceType.COMMERCIAL_SOLAR: [
        "company_name",
        "contact_designation",
        "location",
        "premises_owned",
        "monthly_bill",
        "roof_or_land_available",
        "decision_maker",
        "timeline",
    ],
    ServiceType.INDUSTRIAL_SOLAR: [
        "company_name",
        "industry",
        "location",
        "connection_type",
        "monthly_bill",
        "roof_or_land_available",
        "decision_maker",
        "timeline",
    ],
    ServiceType.AGRICULTURE_SOLAR: [
        "village",
        "mandal",
        "district",
        "land_ownership",
        "land_area",
        "pump_hp",
        "scheme_interest",
    ],
    ServiceType.GROUND_MOUNTED_SOLAR: [
        "location",
        "land_ownership",
        "land_area",
        "monthly_bill",
        "timeline",
    ],
    ServiceType.EXISTING_SOLAR_UPGRADE: [
        "existing_capacity",
        "installation_year",
        "expansion_requirement",
        "location",
    ],
    ServiceType.SOLAR_MAINTENANCE: [
        "existing_capacity",
        "installation_year",
        "issue",
        "location",
        "urgency",
    ],
    ServiceType.PANEL_CLEANING: [
        "existing_capacity",
        "location",
        "urgency",
    ],
    ServiceType.GENERAL_ENQUIRY: ["location", "interest"],
}


def required_fields(service: ServiceType | None) -> list[str]:
    if service is None:
        return []
    return REQUIRED_FIELDS.get(service, [])


def missing_fields(service: ServiceType | None, collected: dict) -> list[str]:
    """Which required fields the customer has not answered yet."""
    return [
        field
        for field in required_fields(service)
        if collected.get(field) in (None, "", [])
    ]


def is_sufficient(service: ServiceType | None, collected: dict) -> bool:
    """Enough gathered to hand the lead to sales?

    A lead does not need every field — the core commercial signals are
    enough to score and route it.
    """
    if service is None:
        return False
    remaining = missing_fields(service, collected)
    return len(remaining) <= max(1, len(required_fields(service)) // 3)


def required_fields_text() -> str:
    """Render the field map for the system prompt."""
    lines = ["## Qualification fields to gather, by service"]
    for service, fields in REQUIRED_FIELDS.items():
        lines.append(f"- {service.value}: {', '.join(fields)}")
    return "\n".join(lines)
