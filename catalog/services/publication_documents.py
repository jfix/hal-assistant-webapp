from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from catalog.models import (
    AuditEvent,
    DocumentPublicationLink,
    DocumentSummaryCache,
    FieldAssertion,
    Publication,
)
from catalog.services.document_summaries import (
    SUMMARY_GENERATOR_VERSION,
    BilingualSummary,
    document_sha256,
    extract_document_text,
    extract_document_title,
    generate_bilingual_summary,
    summary_model,
)
from catalog.services.summary_limits import generation_slot

GENERATED_FIELDS = ("abstract_fr", "keywords_fr", "abstract_en", "keywords_en")


@dataclass(frozen=True)
class CachedGeneration:
    entry: DocumentSummaryCache
    result: BilingualSummary
    cache_hit: bool


def get_or_generate_summary(*, upload, owner) -> CachedGeneration:
    """Return one user's immutable cached analysis, generating it only once."""
    fingerprint = document_sha256(upload)
    model_name = summary_model()
    text = extract_document_text(upload)
    extracted_title = extract_document_title(upload, text)
    source_filename = upload.name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:255]
    cached = DocumentSummaryCache.objects.filter(
        owner=owner,
        document_sha256=fingerprint,
        model_name=model_name,
        generator_version=SUMMARY_GENERATOR_VERSION,
    ).first()
    if cached:
        DocumentSummaryCache.objects.filter(id=cached.id).update(
            source_filename=source_filename,
            document_title=extracted_title,
        )
        cached.source_filename = source_filename
        cached.document_title = extracted_title
        result = BilingualSummary(
            abstract_en=cached.abstract_en,
            abstract_fr=cached.abstract_fr,
            keywords_en=cached.keywords_en,
            keywords_fr=cached.keywords_fr,
            suggested_title=cached.document_title,
            suggested_authors=cached.suggested_authors,
            suggested_publication_year=cached.suggested_publication_year,
            suggested_publication_type=cached.suggested_publication_type,
            suggested_doi=cached.suggested_doi,
        )
        return CachedGeneration(entry=cached, result=result, cache_hit=True)

    with generation_slot(
        owner=owner,
        document_sha256=fingerprint,
        model_name=model_name,
    ):
        result = generate_bilingual_summary(text)
    upload.seek(0)
    entry = DocumentSummaryCache.objects.create(
        owner=owner,
        source_filename=source_filename,
        document_title=result.suggested_title or extracted_title,
        source_file=upload,
        document_sha256=fingerprint,
        model_name=model_name,
        generator_version=SUMMARY_GENERATOR_VERSION,
        abstract_en=result.abstract_en,
        abstract_fr=result.abstract_fr,
        keywords_en=result.keywords_en,
        keywords_fr=result.keywords_fr,
        suggested_authors=result.suggested_authors or [],
        suggested_publication_year=result.suggested_publication_year,
        suggested_publication_type=result.suggested_publication_type,
        suggested_doi=result.suggested_doi,
    )
    return CachedGeneration(entry=entry, result=result, cache_hit=False)


@transaction.atomic
def propose_generated_fields(*, publication: Publication, summary: DocumentSummaryCache, actor):
    """Associate document evidence and propose each changed generated field separately."""
    publication = Publication.objects.select_for_update().get(pk=publication.pk)
    existing_link = DocumentPublicationLink.objects.filter(summary=summary).first()
    if existing_link and existing_link.publication_id != publication.id:
        raise ValueError("Ce document est déjà associé à une autre notice.")

    if existing_link:
        assertions = list(
            summary.field_assertions.filter(publication=publication).order_by("field_path")
        )
        return existing_link, assertions, False

    link = DocumentPublicationLink.objects.create(
        summary=summary,
        publication=publication,
        actor=actor,
        action=DocumentPublicationLink.Action.LINKED,
    )
    assertions = []
    for field_path in GENERATED_FIELDS:
        value = getattr(summary, field_path)
        if value and value != getattr(publication, field_path):
            assertions.append(
                FieldAssertion.objects.create(
                    publication=publication,
                    source_record=None,
                    document_summary=summary,
                    field_path=field_path,
                    value=value,
                    normalized_value="",
                    origin="document_ai",
                    confidence="generated",
                    state=FieldAssertion.State.PROPOSED,
                )
            )
    AuditEvent.objects.create(
        actor=actor,
        action="document.generation_proposed",
        object_type="DocumentSummaryCache",
        object_id=str(summary.id),
        metadata={
            "publication_key": publication.publication_key,
            "proposed_fields": [item.field_path for item in assertions],
            "base_version": publication.version,
        },
    )
    return link, assertions, True
