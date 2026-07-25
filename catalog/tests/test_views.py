from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from catalog.models import Publication, SourceImport, SourceRecord

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


@pytest.fixture
def publication_with_source() -> Publication:
    publication = Publication.objects.create(
        publication_key="pub-xml-1",
        publication_type="journal_article",
        title="A debuggable notice",
        publication_year=2024,
        authors=["Ada Lovelace"],
    )
    source_import = SourceImport.objects.create(
        source_type=SourceImport.SourceType.XLSX,
        source_name="review.xlsx",
        stored_file="snapshots/ab/deadbeef.xlsx",
        file_sha256="d" * 64,
        parser_version="hal-assistant/test",
        report_sha256="e" * 64,
        record_count=1,
        report={},
        retrieved_at=timezone.now(),
    )
    SourceRecord.objects.create(
        source_import=source_import,
        publication=publication,
        locator="Publications!row-2",
        original_citation="Lovelace, Ada. A debuggable notice. 2024.",
        raw_data={
            "publication_id": "pub-xml-1",
            "title": "A debuggable notice",
            "document_type": "ART",
            "year": 2024,
            "authors": "Ada Lovelace",
            "hal_domain": "shs.litt",
            "idhal": "florence-fix",
        },
        record_sha256="f" * 64,
    )
    return publication


def test_submission_xml_debug_view_renders_tei(
    client,
    user,
    publication_with_source,
) -> None:
    client.force_login(user)

    response = client.get(
        reverse("publication-xml", args=[publication_with_source.id]),
    )

    assert response.status_code == 200
    content = response.content.decode()
    # The notice is HTML-escaped for safe display on the page.
    assert "&lt;TEI" in content
    assert "Debug preview" in content


def test_submission_xml_raw_format_returns_xml(
    client,
    user,
    publication_with_source,
) -> None:
    client.force_login(user)

    response = client.get(
        reverse("publication-xml", args=[publication_with_source.id]),
        {"format": "raw"},
    )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/xml")
    assert b"<TEI" in response.content


def test_submission_xml_requires_authentication(client, publication_with_source) -> None:
    response = client.get(
        reverse("publication-xml", args=[publication_with_source.id]),
    )

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))
