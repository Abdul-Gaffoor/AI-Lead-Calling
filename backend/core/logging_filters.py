"""PII masking in logs (MVP section 32).

Every log line this platform writes is about a person who did not consent
to being in a log file. Phone numbers are the whole identity model here —
`customers.phone` is the primary key in all but name — so a stack trace
carrying one, shipped to a log aggregator, is a copy of the customer
database leaking a row at a time.

This filter rewrites records as they are emitted. It is not a substitute
for not logging personal data in the first place; it is the net under
that, for the exception handler nobody anticipated and the third-party
library that logs its request body.

What it masks, and what it deliberately leaves alone:

* **Indian mobile numbers** keep their last two digits — `+91XXXXXX67` —
  because an operator reading a log needs to tell two calls apart, and two
  digits is not an identity.
* **Email addresses** keep the first character and the domain, for the
  same reason.
* **Aadhaar-shaped 12-digit runs** are masked entirely. There is no
  operational reason to see one and a very large reason not to.
* **Transcripts and names are not touched.** A filter cannot find a name
  reliably, and one that half-worked would be worse than an honest
  boundary: call content belongs in the recordings store, behind the role
  check and the audit log, and never in a log line.
"""

from __future__ import annotations

import logging
import re

#: +91 98765 43210, 09876543210, 9876543210 — with or without separators.
_PHONE = re.compile(r"(?<!\d)((?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5})(?!\d)")
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
#: Twelve digits in a row, optionally grouped in fours.
_AADHAAR = re.compile(r"(?<![\d+])\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")


def mask_phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(1))
    return f"+91{'X' * 8}{digits[-2:]}" if len(digits) >= 2 else "+91XXXXXXXXXX"


def mask(text: str) -> str:
    """Redact personal identifiers in `text`."""
    if not text:
        return text
    # Phones first. "+919876543210" is twelve digits and the Aadhaar
    # pattern would otherwise claim the most common identifier in this
    # system, labelling every call log line as an Aadhaar number.
    text = _PHONE.sub(mask_phone, text)
    text = _AADHAAR.sub("[AADHAAR]", text)
    return _EMAIL.sub(lambda m: f"{m.group(1)}***{m.group(2)}", text)


class PIIFilter(logging.Filter):
    """Masks personal data in log messages and their arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Mask the arguments rather than the formatted message where we
            # can, so the record keeps its structure for a JSON handler.
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        key: mask(value) if isinstance(value, str) else value
                        for key, value in record.args.items()
                    }
                else:
                    record.args = tuple(
                        mask(value) if isinstance(value, str) else value
                        for value in record.args
                    )
            if isinstance(record.msg, str):
                record.msg = mask(record.msg)
        except Exception:
            # A logging filter that raises loses the log line it was
            # protecting. Letting an unmasked line through is bad; losing
            # the error entirely is worse.
            pass
        return True


def install(root: logging.Logger | None = None) -> PIIFilter:
    """Attach the filter to every handler on the root logger.

    Filters on a logger do not apply to records from its children, but
    filters on a *handler* see everything that handler writes — which is
    what we want, since the leak could come from any module.
    """
    logger = root or logging.getLogger()
    pii = PIIFilter()
    for handler in logger.handlers:
        if not any(isinstance(f, PIIFilter) for f in handler.filters):
            handler.addFilter(pii)
    return pii
