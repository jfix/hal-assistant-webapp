from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from catalog.models import (
    AssertionDecision,
    AuditEvent,
    DocumentPublicationLink,
    DocumentSummaryCache,
    FieldAssertion,
    Publication,
)
from catalog.services.document_matching import find_publication_matches
from catalog.services.publication_documents import (
    CachedGeneration,
    propose_generated_fields,
)
from catalog.services.review import decide_assertion

pytestmark = pytest.mark.django_db


def _user(username: str, *, reviewer: bool = False):
    user = get_user_model().objects.create_user(username=username, password="test-password")
    if reviewer:
        user.user_permissions.add(Permission.objects.get(codename="review_publication"))
    return user


def _summary(owner, **overrides):
    values = {
        "owner": owner,
        "source_filename": "article.pdf",
        "document_title": "Memory and the European Stage",
        "document_sha256": "a" * 64,
        "model_name": "test-model",
        "generator_version": "test-version",
        "abstract_en": "English abstract",
        "abstract_fr": "Résumé français",
        "keywords_en": ["memory"],
        "keywords_fr": ["mémoire"],
        "suggested_authors": ["Florence Fix"],
        "suggested_publication_year": 2024,
        "suggested_publication_type": "article",
    }
    values.update(overrides)
    return DocumentSummaryCache.objects.create(**values)


def _publication(**overrides):
    values = {
        "publication_key": "existing-1",
        "publication_type": "article",
        "title": "Memory and the European Stage",
        "publication_year": 2024,
        "authors": ["Florence Fix"],
    }
    values.update(overrides)
    return Publication.objects.create(**values)


def test_matching_combines_title_author_and_year() -> None:
    user = _user("matcher")
    summary = _summary(user)
    publication = _publication()

    match = find_publication_matches(summary)[0]

    assert match.publication == publication
    assert match.score == 100
    assert match.reasons == ("même titre", "auteur en commun", "même année")


def test_reviewer_can_link_document_and_generated_metadata(client) -> None:
    user = _user("reviewer", reviewer=True)
    summary = _summary(user)
    publication = _publication()
    client.force_login(user)

    response = client.post(
        reverse("document-summary-link", args=[summary.id]),
        {"publication_id": publication.id},
    )

    assert response.status_code == 302
    link = DocumentPublicationLink.objects.get(summary=summary)
    publication.refresh_from_db()
    assert link.publication == publication
    assert link.action == DocumentPublicationLink.Action.LINKED
    assert AuditEvent.objects.filter(action="document.linked").exists()
    assert publication.abstract_en == "English abstract"
    assert publication.abstract_fr == "Résumé français"
    assert publication.keywords_en == ["memory"]
    assert publication.keywords_fr == ["mémoire"]
    assert publication.version == 2


def test_non_reviewer_cannot_link_document(client) -> None:
    user = _user("ordinary")
    summary = _summary(user)
    publication = _publication()
    client.force_login(user)

    client.post(
        reverse("document-summary-link", args=[summary.id]),
        {"publication_id": publication.id},
    )

    assert not DocumentPublicationLink.objects.filter(summary=summary).exists()


def test_create_draft_is_local_and_uses_only_suggested_fields(client) -> None:
    user = _user("creator", reviewer=True)
    summary = _summary(
        user,
        document_sha256="b" * 64,
        document_title="A New Archival Study",
        suggested_publication_year=2023,
    )
    client.force_login(user)

    response = client.post(reverse("document-summary-create-publication", args=[summary.id]))

    publication = DocumentPublicationLink.objects.get(summary=summary).publication
    assert response.status_code == 302
    assert publication.publication_key == "document-bbbbbbbbbbbbbbbb"
    assert publication.review_state == Publication.ReviewState.DRAFT
    assert publication.hal_id == ""
    assert publication.hal_status == ""
    assert publication.abstract_en == "English abstract"
    assert publication.keywords_fr == ["mémoire"]


def test_linking_preserves_existing_edited_abstracts_and_keywords(client) -> None:
    user = _user("preserve-editor", reviewer=True)
    summary = _summary(user)
    publication = _publication(
        abstract_en="Existing edited abstract",
        keywords_en=["existing"],
    )
    client.force_login(user)

    client.post(
        reverse("document-summary-link", args=[summary.id]),
        {"publication_id": publication.id},
    )

    publication.refresh_from_db()
    assert publication.abstract_en == "Existing edited abstract"
    assert publication.keywords_en == ["existing"]
    assert publication.abstract_fr == "Résumé français"
    assert publication.keywords_fr == ["mémoire"]


def test_probable_duplicate_blocks_new_draft(client) -> None:
    user = _user("duplicate-check", reviewer=True)
    summary = _summary(user)
    _publication()
    client.force_login(user)

    client.post(reverse("document-summary-create-publication", args=[summary.id]))

    assert Publication.objects.count() == 1
    assert not DocumentPublicationLink.objects.filter(summary=summary).exists()


def test_document_generation_creates_independent_proposals_without_overwriting() -> None:
    user = _user("document-proposer", reviewer=True)
    summary = _summary(user)
    publication = _publication(
        abstract_en="Existing English abstract",
        abstract_fr="Résumé français existant",
        keywords_en=["existing"],
        keywords_fr=["existant"],
    )

    link, assertions, created = propose_generated_fields(
        publication=publication,
        summary=summary,
        actor=user,
    )

    publication.refresh_from_db()
    assert created is True
    assert link.publication == publication
    assert publication.abstract_en == "Existing English abstract"
    assert publication.keywords_fr == ["existant"]
    assert {item.field_path for item in assertions} == {
        "abstract_en",
        "abstract_fr",
        "keywords_en",
        "keywords_fr",
    }
    assert all(item.document_summary == summary for item in assertions)
    assert all(item.source_record is None for item in assertions)
    assert AuditEvent.objects.filter(action="document.generation_proposed").exists()


def test_accepting_document_proposal_marks_published_notice_modified() -> None:
    user = _user("published-document-reviewer", reviewer=True)
    summary = _summary(user)
    publication = _publication(
        abstract_en="Old abstract",
        hal_id="hal-01234567",
        version=4,
        hal_synced_version=4,
    )
    _link, assertions, _created = propose_generated_fields(
        publication=publication,
        summary=summary,
        actor=user,
    )
    abstract_proposal = next(
        item for item in assertions if item.field_path == "abstract_en"
    )

    decide_assertion(
        assertion=abstract_proposal,
        actor=user,
        outcome=AssertionDecision.Outcome.ACCEPTED,
        base_version=4,
    )

    publication.refresh_from_db()
    assert publication.abstract_en == "English abstract"
    assert publication.version == 5
    assert publication.hal_synced_version == 4
    assert publication.workflow_statuses[2]["label"] == "Modifié"


def test_reviewer_can_generate_proposals_from_publication_page(client) -> None:
    user = _user("record-uploader", reviewer=True)
    summary = _summary(user)
    publication = _publication(abstract_en="Existing abstract")
    generation = CachedGeneration(
        entry=summary,
        result=None,  # The view only needs the immutable cache entry here.
        cache_hit=False,
    )
    client.force_login(user)

    with patch(
        "catalog.views.get_or_generate_summary",
        return_value=generation,
    ):
        response = client.post(
            reverse("publication-generate-from-document", args=[publication.id]),
            {"document": SimpleUploadedFile("article.pdf", b"test", "application/pdf")},
        )

    assert response.status_code == 302
    assert response.url == reverse("publication-detail", args=[publication.id])
    assert DocumentPublicationLink.objects.filter(
        summary=summary, publication=publication
    ).exists()
    assert FieldAssertion.objects.filter(
        publication=publication,
        document_summary=summary,
        state=FieldAssertion.State.PROPOSED,
    ).count() == 4


def test_non_reviewer_cannot_generate_or_associate_document(client) -> None:
    user = _user("record-reader")
    publication = _publication()
    client.force_login(user)

    with patch("catalog.views.get_or_generate_summary") as generate:
        response = client.post(
            reverse("publication-generate-from-document", args=[publication.id]),
            {"document": SimpleUploadedFile("article.pdf", b"test", "application/pdf")},
        )

    assert response.status_code == 302
    generate.assert_not_called()
    assert not DocumentPublicationLink.objects.exists()
    assert not FieldAssertion.objects.exists()


def test_generation_action_is_prominent_only_when_generated_fields_are_missing(client) -> None:
    user = _user("adaptive-generator", reviewer=True)
    publication = _publication()
    client.force_login(user)

    missing_response = client.get(
        reverse("publication-detail", args=[publication.id])
    ).content.decode()
    publication.abstract_en = "English"
    publication.abstract_fr = "Français"
    publication.keywords_en = ["keyword"]
    publication.keywords_fr = ["mot-clé"]
    publication.save()
    complete_response = client.get(
        reverse("publication-detail", args=[publication.id])
    ).content.decode()

    assert "document-generation-callout" in missing_response
    assert "Compléter depuis votre article" in missing_response
    assert "document-generation-discreet" not in missing_response
    assert "document-generation-callout" not in complete_response
    assert "document-generation-discreet" in complete_response
    assert "Générer depuis un document" in complete_response


def test_reviewer_can_search_for_an_unsuggested_existing_publication(client) -> None:
    user = _user("manual-matcher", reviewer=True)
    wanted = _publication(
        publication_key="manual-match",
        title="A Distant but Known Publication",
        authors=["Known Scholar"],
        publication_year=2019,
        hal_document_type="ART",
        hal_id="hal-09876543",
    )
    _publication(
        publication_key="manual-other",
        title="An Unrelated Record",
    )
    client.force_login(user)

    response = client.get(
        reverse("publication-search"),
        {"q": "Distant"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {
                "id": str(wanted.id),
                "title": "A Distant but Known Publication",
                "authors": ["Known Scholar"],
                "year": 2019,
                "hal_type": "ART",
                "hal_id": "hal-09876543",
            }
        ]
    }


def test_publication_search_is_permission_gated_and_requires_two_characters(client) -> None:
    user = _user("manual-search-reader")
    client.force_login(user)

    forbidden = client.get(reverse("publication-search"), {"q": "Known"})
    user.user_permissions.add(Permission.objects.get(codename="review_publication"))
    short_query = client.get(reverse("publication-search"), {"q": "K"})

    assert forbidden.status_code == 403
    assert short_query.status_code == 200
    assert short_query.json() == {"results": []}


def test_document_matching_page_offers_manual_typeahead_before_new_draft(client) -> None:
    user = _user("manual-typeahead", reviewer=True)
    summary = _summary(user)
    client.force_login(user)

    content = client.get(
        reverse("document-summary-cache-detail", args=[summary.id])
    ).content.decode()

    assert "Rechercher une autre notice" in content
    assert 'data-publication-search data-search-url="/publications/search/"' in content
    assert "data-publication-link-form" in content
    assert content.index("Rechercher une autre notice") < content.index(
        "Créer un nouveau brouillon local"
    )
