"""Конфигурација на апликацијата, читана од околината (.env)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Сите поставки доаѓаат од променливи на околината; види .env.example."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Апликација ----
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    secret_key: str = Field(min_length=16)

    # ---- PostgreSQL ----
    postgres_db: str = "marketchecker"
    postgres_user: str = "marketchecker"
    postgres_password: str
    postgres_host: str = "db"
    postgres_port: int = 5432

    # ---- Читачи на ценовници (учтиво читање) ----
    scraper_user_agent: str = "Marketchecker/0.1"
    scraper_delay_seconds: float = Field(default=1.5, ge=0)
    scraper_max_retries: int = Field(default=3, ge=0)
    scraper_timeout_seconds: float = Field(default=30.0, gt=0)

    # ---- Дневно закажување ----
    scheduler_enabled: bool = True
    scheduler_timezone: str = "Europe/Skopje"
    scheduler_hour: int = Field(default=11, ge=0, le=23)
    scheduler_minute: int = Field(default=0, ge=0, le=59)

    @field_validator("scheduler_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)  # фрла грешка ако зоната не постои
        return value

    @property
    def tz(self) -> ZoneInfo:
        """Временска зона во која се смета „денешниот“ ценовник."""
        return ZoneInfo(self.scheduler_timezone)

    @property
    def database_url(self) -> str:
        """Async DSN за SQLAlchemy/asyncpg."""
        return self._dsn(self.postgres_db)

    @property
    def test_database_url(self) -> str:
        """Одделна база за тестови, за да не ги гази развојните податоци."""
        return self._dsn(f"{self.postgres_db}_test")

    def _dsn(self, database: str) -> str:
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Кеширана инстанца; користи ја ова наместо да креираш Settings() директно."""
    return Settings()  # type: ignore[call-arg]
