from __future__ import annotations

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from hal_webapp import settings as project_settings


def test_sessions_expire_after_one_hour_and_when_browser_closes() -> None:
    assert settings.SESSION_COOKIE_AGE == 3600
    assert settings.SESSION_EXPIRE_AT_BROWSER_CLOSE is True
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_SAMESITE == "Lax"


def test_production_validation_rejects_broad_hosts(monkeypatch) -> None:
    monkeypatch.setattr(project_settings, "DEBUG", False)
    monkeypatch.setattr(project_settings, "SECRET_KEY", "s" * 60)
    monkeypatch.setattr(project_settings, "ALLOWED_HOSTS", [".workers.dev"])
    monkeypatch.setattr(
        project_settings,
        "CSRF_TRUSTED_ORIGINS",
        ["https://*.workers.dev"],
    )
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", ".workers.dev")
    monkeypatch.setenv("OPENAI_API_KEY", "configured")

    with pytest.raises(ImproperlyConfigured, match="exact hostnames"):
        project_settings.validate_production_configuration()


def test_production_validation_accepts_exact_secure_configuration(monkeypatch) -> None:
    monkeypatch.setattr(project_settings, "DEBUG", False)
    monkeypatch.setattr(project_settings, "SECRET_KEY", "s" * 60)
    monkeypatch.setattr(
        project_settings,
        "ALLOWED_HOSTS",
        ["summaries.example.org"],
    )
    monkeypatch.setattr(
        project_settings,
        "CSRF_TRUSTED_ORIGINS",
        ["https://summaries.example.org"],
    )
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "summaries.example.org")
    monkeypatch.setenv("OPENAI_API_KEY", "configured")

    project_settings.validate_production_configuration()


def test_production_validation_accepts_multiple_exact_hosts(monkeypatch) -> None:
    monkeypatch.setattr(project_settings, "DEBUG", False)
    monkeypatch.setattr(project_settings, "SECRET_KEY", "s" * 60)
    monkeypatch.setattr(
        project_settings,
        "ALLOWED_HOSTS",
        ["hal.jfix.com", "hal-publication-manager.example.workers.dev"],
    )
    monkeypatch.setattr(
        project_settings,
        "CSRF_TRUSTED_ORIGINS",
        [
            "https://hal.jfix.com",
            "https://hal-publication-manager.example.workers.dev",
        ],
    )
    monkeypatch.setenv(
        "DJANGO_ALLOWED_HOSTS",
        "hal.jfix.com,hal-publication-manager.example.workers.dev",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "configured")

    project_settings.validate_production_configuration()


@pytest.mark.parametrize(
    ("secret", "api_key", "message"),
    [
        ("short", "configured", "at least 50 characters"),
        ("s" * 60, "", "OPENAI_API_KEY"),
    ],
)
def test_production_validation_rejects_weak_or_missing_secrets(
    monkeypatch, secret, api_key, message
) -> None:
    monkeypatch.setattr(project_settings, "DEBUG", False)
    monkeypatch.setattr(project_settings, "SECRET_KEY", secret)
    monkeypatch.setattr(project_settings, "ALLOWED_HOSTS", ["summaries.example.org"])
    monkeypatch.setattr(
        project_settings,
        "CSRF_TRUSTED_ORIGINS",
        ["https://summaries.example.org"],
    )
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "summaries.example.org")
    monkeypatch.setenv("OPENAI_API_KEY", api_key)

    with pytest.raises(ImproperlyConfigured, match=message):
        project_settings.validate_production_configuration()
