"""Indian mobile number validation and normalization (MVP section 3)."""

import re

_STRIP_RE = re.compile(r"[\s\-().]")


def normalize_indian_mobile(raw: str | None) -> str | None:
    """Normalize a phone number to +91XXXXXXXXXX, or return None if invalid.

    Accepts common formats: 9876543210, 09876543210, 919876543210,
    +91 98765 43210, 98765-43210, etc. Indian mobile numbers are 10 digits
    starting with 6-9.
    """
    if raw is None:
        return None
    value = _STRIP_RE.sub("", str(raw).strip())
    if not value:
        return None

    if value.startswith("+"):
        value = value[1:]
    if not value.isdigit():
        return None

    if len(value) == 12 and value.startswith("91"):
        value = value[2:]
    elif len(value) == 11 and value.startswith("0"):
        value = value[1:]

    if len(value) != 10 or value[0] not in "6789":
        return None
    return f"+91{value}"
