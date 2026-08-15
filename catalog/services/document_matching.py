from __future__ import annotations

from django.db import transaction
from django.utils.translation import gettext as _

from catalog.models import AuditEvent, DocumentPublicationLink, DocumentSummaryCache, Publication
from catalog.services.matching import PublicationMatch, normalized, score_publication_matches
from catalog.services.publication_readiness import recalculate_hal_readiness


def find_publication_matches(
    summary: DocumentSummaryCache, limit: int = 5
) -> list[PublicationMatch]:
    return score_publication_matches(
        title=normalized(summary.document_title),
        authors={normalized(item) for item in summary.suggested_authors if item},
        doi=normalized(summary.suggested_doi),
        publication_year=summary.suggested_publication_year,
        limit=limit,
    )


@transaction.atomic
def link_summary(*, summary: DocumentSummaryCache, publication: Publication, actor, action: str):
    publication = Publication.objects.select_for_update().get(pk=publication.pk)
    generated_fields = {
        "abstract_en": summary.abstract_en,
        "abstract_fr": summary.abstract_fr,
        "keywords_en": summary.keywords_en,
        "keywords_fr": summary.keywords_fr,
    }
    populated = [
        field_name
        for field_name, value in generated_fields.items()
        if value and not getattr(publication, field_name)
    ]
    if populated:
        for field_name in populated:
            setattr(publication, field_name, generated_fields[field_name])
        publication.version += 1
        recalculate_hal_readiness(publication)
        publication.save(
            update_fields=[
                *populated,
                "version",
                "missing_required_fields",
                "readiness_state",
                "updated_at",
            ]
        )
    link = DocumentPublicationLink.objects.create(
        summary=summary, publication=publication, actor=actor, action=action
    )
    AuditEvent.objects.create(
        actor=actor,
        action=f"document.{action}",
        object_type="DocumentSummaryCache",
        object_id=str(summary.id),
        metadata={
            "publication_key": publication.publication_key,
            "populated_fields": populated,
            "resulting_version": publication.version,
        },
    )
    return link


@transaction.atomic
def create_draft_from_summary(*, summary: DocumentSummaryCache, actor):
    existing = find_publication_matches(summary)
    if any(match.score >= 90 for match in existing):
        raise ValueError(
            _("Une notice correspondante très probable existe déjà ; associez-la au document.")
        )
    key = f"document-{summary.document_sha256[:16]}"
    publication, created = Publication.objects.get_or_create(
        publication_key=key,
        defaults={
            "title": summary.document_title,
            "authors": summary.suggested_authors,
            "publication_year": summary.suggested_publication_year,
            "publication_type": summary.suggested_publication_type or "other",
            "doi": summary.suggested_doi,
            "abstract_en": summary.abstract_en,
            "abstract_fr": summary.abstract_fr,
            "keywords_en": summary.keywords_en,
            "keywords_fr": summary.keywords_fr,
            "review_state": Publication.ReviewState.DRAFT,
            "readiness_state": Publication.ReadinessState.NEEDS_REVIEW,
        },
    )
    return link_summary(
        summary=summary,
        publication=publication,
        actor=actor,
        action=(
            DocumentPublicationLink.Action.CREATED
            if created
            else DocumentPublicationLink.Action.LINKED
        ),
    )
