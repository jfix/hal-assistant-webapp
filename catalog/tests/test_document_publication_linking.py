from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from catalog.models import AuditEvent, DocumentPublicationLink, DocumentSummaryCache, Publication
from catalog.services.document_matching import find_publication_matches

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
    assert link.publication == publication
    assert link.action == DocumentPublicationLink.Action.LINKED
    assert AuditEvent.objects.filter(action="document.linked").exists()


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


def test_probable_duplicate_blocks_new_draft(client) -> None:
    user = _user("duplicate-check", reviewer=True)
    summary = _summary(user)
    _publication()
    client.force_login(user)

    client.post(reverse("document-summary-create-publication", args=[summary.id]))

    assert Publication.objects.count() == 1
    assert not DocumentPublicationLink.objects.filter(summary=summary).exists()
