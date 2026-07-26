from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def test_create_reviewer_sets_up_group_and_permission() -> None:
    call_command("create_reviewer", "florence", "--password", "pw", "--email", "f@example.org")

    user = get_user_model().objects.get(username="florence")
    assert user.groups.filter(name="Réviseurs").exists()
    assert user.has_perm("catalog.review_publication")
    assert user.email == "f@example.org"
    # A reviewer is not an admin.
    assert not user.is_staff
    assert not user.is_superuser


def test_create_reviewer_is_idempotent_for_group_membership() -> None:
    call_command("create_reviewer", "florence", "--password", "pw")
    # Running again must not fail or duplicate membership.
    call_command("create_reviewer", "florence")

    user = get_user_model().objects.get(username="florence")
    assert user.groups.filter(name="Réviseurs").count() == 1
