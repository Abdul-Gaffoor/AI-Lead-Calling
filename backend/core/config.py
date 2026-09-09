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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
