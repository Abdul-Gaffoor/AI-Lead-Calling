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

    # ---- AI voice pipeline (MVP sections 9-11) ----
    # Providers: "mock" makes no network calls and costs nothing.
    speech_provider: str = "mock"  # mock | elevenlabs
    llm_provider: str = "mock"  # mock | claude
    voice_provider: str = "mock"  # mock | elevenlabs

    default_language: str = "te-IN"
    max_conversation_turns: int = 20

    # Claude
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5"
    #: Voice turns are latency-sensitive; low effort keeps replies prompt.
    llm_effort: str = "low"
    llm_max_tokens: int = 1024
    llm_timeout_seconds: float = 12.0

    # ElevenLabs
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_v3"
    elevenlabs_stt_model_id: str = "scribe_v1"
    #: PSTN is 8 kHz; ulaw_8000 avoids a resampling step on the call leg.
    elevenlabs_output_format: str = "mp3_22050_32"
    tts_timeout_seconds: float = 15.0
    stt_timeout_seconds: float = 20.0
    #: Static lines (greeting, closings) are cached rather than re-synthesized.
    tts_cache_entries: int = 200

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
