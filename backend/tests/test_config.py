"""Поставките, и заштитата што го чува серверот од примерни тајни.

Апликацијата досега се вртеше само локално, каде тајните од `.env.example`
не му пречат никому. На сервер пречат: `SECRET_KEY` ги потпишува сесиите, а
примерната вредност стои во јавно репо - значи секој може да потпише сесија.
Полесно е да се заборави отколку да се забележи, затоа не стартува.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REAL_SECRET = "n7QpV2xK9mZ4tR8wL3cY6bN1hG5sD0fJaE"
REAL_PASSWORD = "dolga-lozinka-koja-ne-e-primer"


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "secret_key": REAL_SECRET,
        "postgres_password": REAL_PASSWORD,
        # Без пошта нема најава, па production не стартува без неа.
        "smtp_host": "posta.primer.mk",
        "_env_file": None,  # не чита .env - тестот не зависи од машината
    }
    values.update(overrides)
    return Settings(**values)


# ==========================================================================
# Примерните тајни на сервер
# ==========================================================================
@pytest.mark.parametrize(
    "placeholder",
    [
        "смени-ме-со-случаен-стринг",
        "СМЕНИ-МЕ-СО-СЛУЧАЕН-СТРИНГ",  # големината на буквите не е маскирање
        "  смени-ме-со-случаен-стринг  ",
        "test-secret-key-dolga-najmalku-16",  # тајната од тестовите
    ],
)
def test_production_refuses_a_placeholder_secret_key(placeholder: str) -> None:
    with pytest.raises(ValidationError, match="примерни тајни"):
        _settings(secret_key=placeholder)


@pytest.mark.parametrize(
    "placeholder", ["смени-ме", "change-me", "postgres", "password", "test"]
)
def test_production_refuses_a_placeholder_password(placeholder: str) -> None:
    """Лозинката нема најмала должина, па кратките примери стигаат дотука."""
    with pytest.raises(ValidationError, match="POSTGRES_PASSWORD"):
        _settings(postgres_password=placeholder)


def test_the_message_names_what_is_wrong() -> None:
    """Порака „невалидна конфигурација" би значела барање низ целиот .env."""
    with pytest.raises(ValidationError) as caught:
        _settings(
            secret_key="смени-ме-со-случаен-стринг", postgres_password="смени-ме"
        )
    message = str(caught.value)
    assert "SECRET_KEY" in message
    assert "POSTGRES_PASSWORD" in message
    assert "token_urlsafe" in message  # кажува и КАКО да се поправи


def test_production_accepts_real_secrets() -> None:
    assert _settings().is_production is True


# ==========================================================================
# Без пошта нема најава
# ==========================================================================
def test_production_refuses_to_start_without_mail() -> None:
    """Сметката се отвора и се влегува САМО преку линк по пошта.

    Сервер без пошта значи дека никој не може да влезе - ни сопственикот.
    Подобро да не стартува отколку луѓето да останат заклучени надвор.
    """
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        _settings(smtp_host="")


def test_mail_is_optional_while_developing() -> None:
    """Локално линкот се запишува во дневникот - тестирањето не смее да
    зависи од сервис за пошта.
    """
    settings = _settings(app_env="development", smtp_host="")
    assert settings.mail_enabled is False


def test_mail_counts_as_set_up_when_there_is_a_server() -> None:
    assert _settings().mail_enabled is True


# ==========================================================================
# Локално истите вредности се во ред
# ==========================================================================
def test_development_keeps_working_with_the_example_values() -> None:
    """Инаку `cp .env.example .env` би престанало да работи за развој."""
    settings = _settings(
        app_env="development",
        secret_key="смени-ме-со-случаен-стринг",
        postgres_password="смени-ме",
    )
    assert settings.is_production is False


def test_tests_keep_working_too() -> None:
    settings = _settings(
        app_env="test",
        secret_key="test-secret-key-dolga-najmalku-16",
        postgres_password="test",
    )
    assert settings.is_production is False


# ==========================================================================
# Другото што не смее да се изгуби
# ==========================================================================
def test_a_short_secret_is_refused_everywhere() -> None:
    with pytest.raises(ValidationError):
        _settings(app_env="development", secret_key="kratko")


def test_cyrillic_user_agent_is_refused() -> None:
    """HTTP заглавијата не поддржуваат кирилица - би паднало секое читање."""
    with pytest.raises(ValidationError, match="ASCII"):
        _settings(scraper_user_agent="Маркетчекер/0.1")


def test_unknown_timezone_is_refused() -> None:
    """И го кажува ИМЕТО на поставката - `ZoneInfo` сам фрла гол KeyError."""
    with pytest.raises(ValidationError, match="SCHEDULER_TIMEZONE"):
        _settings(scheduler_timezone="Europe/Nepostoecko")


def test_a_short_secret_never_passes_even_in_production_check() -> None:
    """Кратките примери („secret") паѓаат на должина пред да се стигне до
    списокот - затоа списокот не е единствената заштита.
    """
    with pytest.raises(ValidationError):
        _settings(secret_key="secret")


def test_password_with_special_characters_survives_the_dsn() -> None:
    """Лозинка со @ или / би го скршила URL-то кон базата."""
    settings = _settings(postgres_password="loz@inka/so#znaci")
    assert "loz%40inka%2Fso%23znaci" in settings.database_url
