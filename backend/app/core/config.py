"""Конфигурација на апликацијата, читана од околината (.env)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Вредностите од .env.example и сè што личи на нив. На сервер со овие
# апликацијата НЕ смее да стартува: тајна што стои во јавно репо не е тајна.
PLACEHOLDER_SECRETS = frozenset(
    {
        "смени-ме",
        "смени-ме-со-случаен-стринг",
        "change-me",
        "secret",
        "password",
        "postgres",
        "test",
        "test-secret-key-dolga-najmalku-16",
    }
)


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
        """Непостоечка зона мора да падне како грешка во конфигурацијата.

        `ZoneInfo` фрла `KeyError`, кој pydantic НЕ го претвора во читлива
        порака - се добиваше гол `ZoneInfoNotFoundError` наместо да се каже
        која поставка е погрешна.
        """
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(
                f"SCHEDULER_TIMEZONE={value!r} не е позната временска зона"
            ) from exc
        return value

    @field_validator("scraper_user_agent")
    @classmethod
    def _validate_user_agent(cls, value: str) -> str:
        """HTTP заглавијата мора да бидат ASCII.

        Кирилица во SCRAPER_USER_AGENT би крашнала секое барање кон изворите,
        и тоа дури при самото читање. Подобро апликацијата да не стартува.
        """
        if not value.isascii():
            raise ValueError(
                "SCRAPER_USER_AGENT смее да содржи само ASCII знаци "
                "(HTTP заглавијата не поддржуваат кирилица)"
            )
        return value

    @model_validator(mode="after")
    def _refuse_placeholder_secrets(self) -> Settings:
        """На сервер, примерните тајни ја запираат апликацијата.

        Полесно е да се заборави `SECRET_KEY` отколку да се забележи - а
        последицата е сесија што секој може да ја потпише. Подобро да не
        стартува отколку да работи отворена.
        """
        if self.app_env != "production":
            return self

        weak = [
            name
            for name, value in (
                ("SECRET_KEY", self.secret_key),
                ("POSTGRES_PASSWORD", self.postgres_password),
            )
            if value.strip().lower() in PLACEHOLDER_SECRETS
        ]
        if weak:
            raise ValueError(
                "На production не смее да се работи со примерни тајни: "
                + ", ".join(weak)
                + ". Генерирај со: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self

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
