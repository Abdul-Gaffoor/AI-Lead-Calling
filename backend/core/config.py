from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / .env file."""

    app_name: str = "Swaraj Solar AI Sales Automation Platform"
    database_url: str = "postgresql+psycopg2://swaraj:swaraj@localhost:5432/swaraj_solar"

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_expires_minutes: int = 60
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Dev convenience: create tables on startup instead of running migrations.
    auto_create_tables: bool = False

    # Campaign / calling defaults
    default_timezone: str = "Asia/Kolkata"
    #: Public base URL the telephony provider can reach for status callbacks.
    public_base_url: str = ""
    #: Shared token required on telephony webhooks; empty disables the check
    #: (acceptable in local development only).
    telephony_webhook_token: str = ""

    # Telephony provider: "mock" (no real calls) or "exotel"
    telephony_provider: str = "mock"
    exotel_sid: str = ""
    exotel_api_key: str = ""
    exotel_api_token: str = ""
    exotel_caller_id: str = ""
    exotel_subdomain: str = "api.exotel.com"
    exotel_flow_app_id: str = ""

    # Worker
    redis_url: str = "redis://localhost:6379/0"
    dispatch_interval_seconds: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
