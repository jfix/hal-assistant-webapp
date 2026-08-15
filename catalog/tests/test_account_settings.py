from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from catalog.models import AuditEvent, HALCredential, UserInterfacePreference
from catalog.services.hal_credentials import credentials_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def account_user():
    return get_user_model().objects.create_user(
        username="florence", email="florence@example.com", password="old-password"
    )


def test_account_page_requires_login(client) -> None:
    response = client.get(reverse("account-settings"))
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


def test_user_can_save_encrypted_credentials_without_password_redisplay(
    client, account_user
) -> None:
    client.force_login(account_user)
    response = client.post(
        reverse("account-settings"),
        {
            "action": "save_hal_credentials",
            "login": "florence-hal",
            "password": "very-secret-hal-password",
        },
        follow=True,
    )

    assert response.status_code == 200
    stored = HALCredential.objects.get(user=account_user)
    assert "florence-hal" not in stored.encrypted_login
    assert "very-secret-hal-password" not in stored.encrypted_password
    assert credentials_for(account_user).login == "florence-hal"
    assert credentials_for(account_user).password == "very-secret-hal-password"
    page = response.content.decode()
    assert "florence-hal" in page
    assert "very-secret-hal-password" not in page
    event = AuditEvent.objects.get(action="hal_credentials.created")
    assert event.metadata == {}


def test_users_cannot_view_each_others_credentials(client, account_user) -> None:
    other = get_user_model().objects.create_user(username="jakob", password="password")
    client.force_login(account_user)
    client.post(
        reverse("account-settings"),
        {
            "action": "save_hal_credentials",
            "login": "florence-hal",
            "password": "cross-user-secret-9841",
        },
    )
    client.force_login(other)

    page = client.get(reverse("account-settings")).content.decode()

    assert "florence-hal" not in page
    assert "cross-user-secret-9841" not in page


def test_user_can_delete_credentials_without_secret_in_audit(client, account_user) -> None:
    client.force_login(account_user)
    client.post(
        reverse("account-settings"),
        {"action": "save_hal_credentials", "login": "florence-hal", "password": "one"},
    )

    response = client.post(
        reverse("account-settings"), {"action": "delete_hal_credentials"}, follow=True
    )

    assert response.status_code == 200
    assert not HALCredential.objects.filter(user=account_user).exists()
    event = AuditEvent.objects.get(action="hal_credentials.deleted")
    assert event.metadata == {}


def test_app_password_change_requires_current_password(client, account_user) -> None:
    client.force_login(account_user)
    response = client.post(
        reverse("password-change"),
        {
            "old_password": "wrong",
            "new_password1": "A-new-strong-password-984!",
            "new_password2": "A-new-strong-password-984!",
        },
    )
    account_user.refresh_from_db()
    assert response.status_code == 200
    assert account_user.check_password("old-password")

    response = client.post(
        reverse("password-change"),
        {
            "old_password": "old-password",
            "new_password1": "A-new-strong-password-984!",
            "new_password2": "A-new-strong-password-984!",
        },
    )
    account_user.refresh_from_db()
    assert response.status_code == 302
    assert account_user.check_password("A-new-strong-password-984!")


def test_browser_language_is_used_when_no_preference_is_saved(client, account_user) -> None:
    client.force_login(account_user)

    response = client.get(reverse("account-settings"), HTTP_ACCEPT_LANGUAGE="en-GB,en;q=0.9")

    assert response.status_code == 200
    assert 'lang="en"' in response.content.decode()
    assert "HAL Publication Manager" in response.content.decode()


def test_saved_interface_language_overrides_browser_language(client, account_user) -> None:
    client.force_login(account_user)

    response = client.post(
        reverse("account-settings"),
        {"action": "save_interface_language", "language": "fr"},
        HTTP_ACCEPT_LANGUAGE="en",
        follow=True,
    )

    assert response.status_code == 200
    assert UserInterfacePreference.objects.get(user=account_user).language == "fr"
    assert 'lang="fr"' in response.content.decode()
