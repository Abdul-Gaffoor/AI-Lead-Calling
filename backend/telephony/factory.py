from functools import lru_cache

from backend.core.config import settings
from backend.telephony.base import TelephonyError, TelephonyProvider
from backend.telephony.mock import MockTelephonyProvider


@lru_cache(maxsize=1)
def get_telephony_provider() -> TelephonyProvider:
    """Return the configured provider (cached for the process lifetime)."""
    name = settings.telephony_provider.lower()
    if name == "mock":
        return MockTelephonyProvider()
    if name == "exotel":
        from backend.telephony.exotel import ExotelProvider

        return ExotelProvider()
    raise TelephonyError(f"Unknown telephony provider: {settings.telephony_provider}")


def reset_provider_cache() -> None:
    """Drop the cached provider (used by tests and after config changes)."""
    get_telephony_provider.cache_clear()
