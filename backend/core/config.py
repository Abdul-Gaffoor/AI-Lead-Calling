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

    #: Shared key the Swaraj website uses to post leads (MVP section 2B).
    website_api_key: str = ""

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

    # ---- Call recordings (MVP sections 29, 32) ----
    #: "local" keeps recordings on the server's disk (a Docker volume in
    #: production); "s3" puts them in a bucket.
    storage_provider: str = "local"  # local | s3
    storage_dir: str = "/var/lib/swaraj/recordings"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    #: Download the recording the telephony provider reports on its webhook.
    #: Off by default: the mock places no real calls, and fetching from a URL a
    #: webhook supplied is a network call that should be turned on knowingly.
    fetch_provider_recordings: bool = False
    recording_fetch_timeout_seconds: float = 30.0
    #: Days to keep call audio; 0 keeps it indefinitely. How long customer
    #: voice data may be held is Swaraj's decision, so there is no default
    #: retention beyond "keep" until someone sets one.
    recording_retention_days: int = 0
    #: Refuse anything larger. A qualification call is a few minutes of speech;
    #: far more than this is a misconfiguration, not a recording.
    max_recording_bytes: int = 25 * 1024 * 1024

    # ---- Knowledge base / RAG (MVP section 18) ----
    #: "mock" is a hashed bag-of-words: offline, free, keyword-quality
    #: retrieval. "voyage" is a real multilingual embedding model.
    embedding_provider: str = "mock"  # mock | voyage
    voyage_api_key: str = ""
    embedding_model: str = "voyage-3"
    embedding_timeout_seconds: float = 20.0
    #: How many passages to put in front of the LLM for one question. Small on
    #: purpose: a voice reply is one or two sentences, and every extra passage
    #: is latency on a live call.
    knowledge_top_k: int = 3
    #: Below this cosine similarity a passage is treated as irrelevant. Better
    #: to tell the customer we will confirm than to answer from a poor match.
    knowledge_min_similarity: float = 0.25

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
