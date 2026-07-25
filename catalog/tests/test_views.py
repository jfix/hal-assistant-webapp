from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from catalog.models import Publication

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user(
        username="reviewer",
        password="local-test-password",
    )


@pytest.fixture
def publications() -> tuple[Publication, Publication]:
    first = Publication.objects.create(
        publication_key="pub-0001",
        publication_type="journal_article",
        title="Portable metadata",
        publication_year=2025,
        authors=["Ada Lovelace"],
        readiness_state=Publication.ReadinessState.HAL_READY,
    )
    second = Publication.objects.create(
        publication_key="pub-0002",
        publication_type="conference_paper",
        title="Cloud deployment",
        publication_year=2024,
        authors=["Grace Hopper"],
        readiness_state=Publication.ReadinessState.NEEDS_ENRICHMENT,
        missing_required_fields=["conference_city"],
    )
    return first, second


def test_health_is_public_and_checks_database(client) -> None:
    response = client.get(reverse("health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "reachable"}


@pytest.mark.parametrize("name", ["publication-list", "home"])
def test_catalog_routes_require_authentication(client, name: str) -> None:
    response = client.get(reverse(name))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


def test_authenticated_user_can_filter_publications(client, user, publications) -> None:
    client.force_login(user)

    response = client.get(
        reverse("publication-list"),
        {"q": "Portable", "readiness": Publication.ReadinessState.HAL_READY},
    )

    assert response.status_code == 200
    assert publications[0].title in response.content.decode()
    assert publications[1].title not in response.content.decode()


def test_authenticated_user_can_view_publication_detail(
    client,
    user,
    publications,
) -> None:
    client.force_login(user)

    response = client.get(
        reverse("publication-detail", args=[publications[1].id]),
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Cloud deployment" in content
    assert "conference_city" in content
