"""Time-based one-time passwords for administrators (MVP section 32).

RFC 6238 TOTP over RFC 4226 HMAC-OTP, on the standard library. It is about
forty lines and adding a dependency to avoid them would be a worse trade:
this code is read by anyone auditing how the platform's strongest accounts
are protected, and a vendored implementation is easier to audit than a
pinned package.

Works with any authenticator app — Google Authenticator, Authy, 1Password —
because the shared secret and the `otpauth://` URI are standard.

Two things here are deliberate:

* **A window of one step either side.** Phone clocks drift. Rejecting a
  code because a handset is twenty seconds fast produces support calls and
  teaches people to turn MFA off.
* **Recovery codes, hashed like passwords.** An administrator who loses
  their phone must not need database access to get back in, and a stolen
  database must not yield usable codes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

#: 30-second steps, 6 digits: what every authenticator app assumes.
STEP_SECONDS = 30
DIGITS = 6
#: Accept the neighbouring steps too, for clock drift.
WINDOW = 1

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 5  # 10 hex characters


def generate_secret() -> str:
    """A fresh base32 secret, the format authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    # Authenticator apps strip base32 padding; put it back before decoding.
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**DIGITS)).zfill(DIGITS)


def code_at(secret: str, when: float | None = None) -> str:
    """The code an app would show right now. Used by tests and support."""
    return _hotp(secret, int((when if when is not None else time.time()) // STEP_SECONDS))


def verify(secret: str, code: str, *, when: float | None = None) -> bool:
    """Is `code` valid for `secret`, allowing for clock drift?"""
    if not secret or not code:
        return False
    candidate = code.strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != DIGITS:
        return False

    counter = int((when if when is not None else time.time()) // STEP_SECONDS)
    for drift in range(-WINDOW, WINDOW + 1):
        # compare_digest so a wrong code cannot be found a digit at a time.
        if hmac.compare_digest(_hotp(secret, counter + drift), candidate):
            return True
    return False


def provisioning_uri(secret: str, *, email: str, issuer: str) -> str:
    """The `otpauth://` URI an authenticator app scans as a QR code."""
    label = quote(f"{issuer}:{email}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """One-time codes for an administrator who has lost their phone."""
    return [secrets.token_hex(RECOVERY_CODE_BYTES) for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    """Recovery codes are stored hashed, like passwords.

    SHA-256 rather than bcrypt: these are 40 bits of real randomness, not a
    human-chosen password, so there is nothing for a slow hash to defend
    against and login stays fast.
    """
    return hashlib.sha256(code.strip().lower().encode("utf-8")).hexdigest()


def consume_recovery_code(code: str, hashed: list[str]) -> list[str] | None:
    """Spend a recovery code, returning the remaining ones.

    Returns None when the code is not valid. The matched code is removed:
    a recovery code works exactly once.
    """
    if not code or not hashed:
        return None
    candidate = hash_recovery_code(code)
    for stored in hashed:
        if hmac.compare_digest(stored, candidate):
            return [h for h in hashed if h != stored]
    return None
