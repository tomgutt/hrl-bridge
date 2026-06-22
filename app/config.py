from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tms_host: str
    tms_port: int
    tms_token: str

    bridge_token: str

    tms_read_timeout_s: float = 30.0
    tms_retry_attempts: int = 3
    tms_backoff_initial_ms: int = 250

    log_level: str = "info"


settings = Settings()  # type: ignore[call-arg]
