from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from catalog.forms import ManualPublicationForm
from catalog.models import AuditEvent, Publication
from catalog.services.manual_publications import create_manual_draft

pytestmark = pytest.mark.django_db


def _reviewer(username: str = "manual-creator"):
    user = get_user_model().objects.create_user(username=username, password="test")
    user.user_permissions.add(Permission.objects.get(codename="review_publication"))
    return user


def _data(**overrides):
    values = {
        "hal_document_type": "ART",
        "title": "A New Journal Article",
        "authors": ["Florence Fix"],
        "publication_year": 2025,
        "language": "fr",
        "doi": "",
        "journal_title": "Revue des études théâtrales",
        "book_title": "",
        "conference_title": "",
        "conference_start_date": None,
        "conference_end_date": None,
        "conference_city": "",
        "conference_country": "",
    }
    values.update(overrides)
    return values


def test_manual_form_requires_only_fields_for_selected_hal_type() -> None:
    article = ManualPublicationForm(
        data={
            "hal_document_type": "ART",
            "title": "Article",
            "authors": "Florence Fix",
            "publication_year": 2025,
            "language": "fr",
        }
    )
    book = ManualPublicationForm(
        data={
            "hal_document_type": "OUV",
            "title": "Livre",
            "authors": "Florence Fix",
            "publication_year": 2025,
            "language": "fr",
        }
    )

    assert article.is_valid() is False
    assert "journal_title" in article.errors
    assert book.is_valid() is True


def test_manual_draft_is_local_ready_and_audited() -> None:
    user = _reviewer()

    publication = create_manual_draft(
        data=_data(),
        actor=user,
        duplicate_reviewed=False,
    )

    assert publication.publication_key.startswith("manual-")
    assert publication.publication_type == "journal_article"
    assert publication.hal_document_type == "ART"
    assert publication.review_state == Publication.ReviewState.DRAFT
    assert publication.readiness_state == Publication.ReadinessState.HAL_READY
    assert publication.hal_id == ""
    assert AuditEvent.objects.filter(
        action="publication.manual_created",
        object_id=str(publication.id),
    ).exists()


def test_manual_draft_preserves_selected_hal_journal_metadata() -> None:
    user = _reviewer("journal-reference-creator")

    publication = create_manual_draft(
        data=_data(
            journal_hal_id="12345",
            journal_issn="1234-5678",
            journal_publisher="Éditions Exemple",
        ),
        actor=user,
        duplicate_reviewed=False,
    )

    assert publication.journal_hal_id == "12345"
    assert publication.issn == ["1234-5678"]
    assert publication.publisher == "Éditions Exemple"


def test_probable_duplicate_blocks_manual_creation_even_after_confirmation() -> None:
    user = _reviewer("duplicate-creator")
    Publication.objects.create(
        publication_key="existing-probable",
        publication_type="journal_article",
        hal_document_type="ART",
        title="A New Journal Article",
        authors=["Florence Fix"],
        publication_year=2025,
    )

    with pytest.raises(ValueError, match="très probable"):
        create_manual_draft(
            data=_data(),
            actor=user,
            duplicate_reviewed=True,
        )

    assert Publication.objects.count() == 1


def test_title_only_candidate_requires_review_but_can_be_rejected() -> None:
    user = _reviewer("candidate-reviewer")
    Publication.objects.create(
        publication_key="existing-title-only",
        publication_type="journal_article",
        title="A New Journal Article",
        authors=["Another Person"],
        publication_year=1999,
    )

    with pytest.raises(ValueError, match="doivent être examinés"):
        create_manual_draft(
            data=_data(),
            actor=user,
            duplicate_reviewed=False,
        )
    created = create_manual_draft(
        data=_data(),
        actor=user,
        duplicate_reviewed=True,
    )

    assert created.title == "A New Journal Article"
    assert Publication.objects.count() == 2


def test_reviewer_can_create_manual_draft_through_guided_form(client) -> None:
    user = _reviewer("form-creator")
    client.force_login(user)

    response = client.post(
        reverse("publication-create-manual"),
        {
            "hal_document_type": "ART",
            "title": "Created Through the Form",
            "authors": "Florence Fix ; Jakob Fix",
            "publication_year": 2024,
            "language": "fr",
            "journal_title": "Revue existante",
        },
    )

    publication = Publication.objects.get(title="Created Through the Form")
    assert response.status_code == 302
    assert response.url == reverse("publication-detail", args=[publication.id])
    assert publication.authors == ["Florence Fix", "Jakob Fix"]


def test_manual_form_shows_adaptive_fields_and_existing_value_suggestions(client) -> None:
    user = _reviewer("suggestion-viewer")
    Publication.objects.create(
        publication_key="suggestion-source",
        publication_type="conference_paper",
        title="Existing conference paper",
        conference_title="Congrès des humanités",
        conference_city="Paris",
        conference_country="France",
    )
    client.force_login(user)

    content = client.get(reverse("publication-create-manual")).content.decode()

    assert "ART — Article dans une revue" in content
    assert 'data-hal-fields="ART"' in content
    assert 'data-hal-fields="COUV"' in content
    assert 'data-hal-fields="COMM"' in content
    assert '<option value="Congrès des humanités">' in content
    assert '<option value="Paris">' in content
    assert 'data-reference-typeahead="journal"' in content
    assert 'data-reference-typeahead="book"' in content
    assert reverse("publication-reference-search") in content
    assert 'id="journal-suggestions"' not in content


@patch("catalog.views.search_hal_references")
def test_reference_search_combines_hal_and_local_results(search_hal, client) -> None:
    from catalog.services.hal_references import HALReferenceSuggestion

    user = _reviewer("reference-searcher")
    Publication.objects.create(
        publication_key="local-journal",
        publication_type="journal_article",
        title="Local article",
        journal_title="Revue locale",
    )
    search_hal.return_value = [
        HALReferenceSuggestion(
            value="Revue HAL",
            source="HAL",
            hal_id="42",
            issn="1234-5678",
        )
    ]
    client.force_login(user)

    response = client.get(
        reverse("publication-reference-search"),
        {"kind": "journal", "q": "revue"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "value": "Revue HAL",
            "source": "HAL",
            "hal_id": "42",
            "issn": "1234-5678",
            "publisher": "",
            "humanities": False,
        },
        {
            "value": "Revue locale",
            "source": "Corpus local",
            "hal_id": "",
            "issn": "",
            "publisher": "",
            "humanities": False,
        },
    ]


def test_reference_search_requires_reviewer_permission(client) -> None:
    user = get_user_model().objects.create_user(
        username="reference-reader", password="test"
    )
    client.force_login(user)

    response = client.get(
        reverse("publication-reference-search"),
        {"kind": "journal", "q": "revue"},
    )

    assert response.status_code == 403


@patch("catalog.views.search_hal_references", return_value=[])
def test_author_reference_search_falls_back_to_local_author_names(
    search_hal, client
) -> None:
    user = _reviewer("author-searcher")
    Publication.objects.create(
        publication_key="local-authors",
        publication_type="book",
        title="Local book",
        authors=["Florence Fix", "Jakob Fix"],
    )
    client.force_login(user)

    response = client.get(
        reverse("publication-reference-search"),
        {"kind": "author", "q": "flor"},
    )

    assert response.status_code == 200
    assert [item["value"] for item in response.json()["results"]] == ["Florence Fix"]
    search_hal.assert_called_once_with("author", "flor")


def test_manual_creation_requires_reviewer_permission(client) -> None:
    user = get_user_model().objects.create_user(username="manual-reader", password="test")
    client.force_login(user)

    response = client.get(reverse("publication-create-manual"))

    assert response.status_code == 302
    assert response.url == reverse("home")
