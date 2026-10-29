from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from cryptography.fernet import Fernet
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

ROOT_DIR = Path(__file__).resolve().parents[3]

_KEY_SNIPPET = (
    "from cryptography.fernet import Fernet; "
    "print(Fernet.generate_key().decode())"
)


class Settings(BaseSettings):
    APP_NAME: str
    APP_ENV: str

    DB_USER: str
    DB_PASSWORD: SecretStr
    DB_NAME: str
    DB_HOST: str
    DB_PORT: int

    SECRET_KEY: SecretStr
    TOKEN_ENCRYPTION_KEY: SecretStr

    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: SecretStr
    GOOGLE_AUTH_REDIRECT_URI: str
    GOOGLE_GMAIL_REDIRECT_URI: str

    FRONTEND_URL: str

    SESSION_COOKIE_NAME: str
    SESSION_EXPIRE_MINUTES: int
    REFRESH_TOKEN_EXPIRE_DAYS: int
    REFRESH_REUSE_GRACE_SECONDS: int

    COOKIE_SECURE: bool
    COOKIE_SAMESITE: str

    AUTH_RATE_LIMIT_PER_MINUTE: int

    SYNC_WINDOW_DAYS: int
    SYNC_MAX_INBOX_MESSAGES: int
    SYNC_MAX_SENT_MESSAGES: int
    SYNC_BATCH_SIZE: int
    SYNC_STALE_MINUTES: int
    SYNC_INTERVAL_MINUTES: int
    LLM_PROVIDER: str
    LLM_MODEL: str

    LLM_FALLBACK_PROVIDER: str | None = None
    LLM_FALLBACK_MODEL: str | None = None

    LLM_TIMEOUT_SECONDS: int = 60

    GROQ_API_KEY: SecretStr | None = None

    ANALYSIS_INTERVAL_MINUTES: int = 2
    ANALYSIS_BATCH_LIMIT: int = 30
    ANALYSIS_CONCURRENCY: int = 1
    ANALYSIS_PAUSE_SECONDS: float = 5.0
    ANALYZE_RATE_PER_MINUTE: int = 6
    APP_TIMEZONE: str = "Asia/Dhaka"
    ENABLE_DEBUG_ENDPOINTS: bool = False

    CORS_ORIGINS: list[str]

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("TOKEN_ENCRYPTION_KEY")
    @classmethod
    def validate_fernet_key(cls, value: SecretStr) -> SecretStr:
        try:
            _ = Fernet(value.get_secret_value().encode())
        except (ValueError, TypeError):
            raise ValueError(
                " ".join(
                    (
                        "TOKEN_ENCRYPTION_KEY must be a valid Fernet key.",
                        "Generate one with:",
                        f'python -c "{_KEY_SNIPPET}"',
                    )
                )
            ) from None

        return value

    @property
    def database_url(self) -> str:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD.get_secret_value(),
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]